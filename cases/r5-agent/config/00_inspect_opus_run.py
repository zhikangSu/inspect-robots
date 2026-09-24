#!/usr/bin/env python3
"""One attended API-driven cube-to-plate trial using native inspect-robots agent.

Only the runner owns CAN/cameras. Native wire capture/transcript/action sidecars
feed the current GitHub Pages exporter. R5 extensions provide calibrated math,
explicit visual checkpoints and the already-validated commanded interpolation base.
"""
import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import shutil
import subprocess
import importlib.metadata
import sys
import time

import httpx
import numpy as np
from benchmark_common import stamp,write_json
from inspect_codex_run import ObservedStartArm,CalibratedCameras
from inspect_robots import eval as ir_eval
from inspect_robots.approver import ChainApprover,ClampApprover,DeltaLimitApprover
from inspect_robots.defaults import load_defaults
from inspect_robots.errors import SafetyAbort
from inspect_robots.scene import Scene
from inspect_robots.scorer import episode_length
from inspect_robots.task import Task
from inspect_robots.types import Action,ActionChunk,Observation,StepResult
from inspect_robots_agent import LLMAgentPolicy
from inspect_robots_agent._capture import WireCapture
from inspect_robots_agent._llm import ToolCall
from inspect_robots_agent._tools import ToolResult
from inspect_robots_arx_r5 import ArxR5Config
from inspect_claude_driver.policy import _png_write
import r5_kin as K

HERE=Path(__file__).resolve().parent
MODEL='claude-opus-5-5'
GOAL='把当前桌面上的木块cube抓起，放入蓝色盘子。闭爪后先短抬并查看两路图确认握住，再搬运；低位松爪并撤离后两次观测确认cube完整稳定留在盘内，调用mark_placement。只做入盘，不从盘子取回。随后显式安全回撤、分段回到折叠起始姿态、确认空爪后闭合，再done。'


class Journal:
    def __init__(self,root):
        self.root=root;self.phase='A';self.seq=0
        self.fp=(root/'events.jsonl').open('x')
    def emit(self,kind,**data):
        self.seq+=1;r=dict(stamp(),event_id=self.seq,phase=self.phase,kind=kind,**data)
        self.fp.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n');self.fp.flush()
        return r


class Rig(ObservedStartArm):
    def __init__(self,cfg,root,journal,fake=False):
        kwargs={'camera_reader':CalibratedCameras(cfg.camera_devices,width=640,height=480)}
        if fake:
            from inspect_dryrun import FakeArm,fake_cameras
            kwargs={'driver_factory':lambda _: {'left':FakeArm()},'camera_reader':fake_cameras(list(cfg.camera_devices),480,640)}
        super().__init__(cfg,**kwargs)
        self.root,self.journal,self.fake=root,journal,fake
        self.info=dataclasses.replace(self.info,is_simulated=fake)
        self.deadline_ns=None;self.enabled=False;self.obs_seq=0
        self.attempts=0;self.closed_intent=False;self.awaiting_grasp=False;self.retained=False
        self.released=False;self.release_obs=None;self.placement=None;self.presented={}
        self.goal_start=None;self.current=None

    def snapshot(self,obs):
        self.obs_seq+=1;self.current=obs
        directory=self.root/'observations';directory.mkdir(exist_ok=True)
        images={}
        for name,img in obs.images.items():
            path=directory/f'{self.obs_seq:06d}_{name}.png';_png_write(path,np.asarray(img));images[name]=str(path)
        q=np.asarray(obs.state['joint_pos']);commanded=self.last_sent.copy()
        row=dict(stamp(),seq=self.obs_seq,phase=self.journal.phase,env_step=obs.extra.get('env_step',self.num_steps),
                 state=q.tolist(),commanded=commanded.tolist(),measured_geometry=self.check.pose(q[:6]),
                 commanded_geometry=self.check.pose(commanded[:6]),images=images)
        write_json(directory/f'{self.obs_seq:06d}.json',row);write_json(self.root/'latest.json',row)
        self.presented[self.obs_seq]=row
        self.journal.emit('observation',**{k:v for k,v in row.items() if k not in ('phase','utc','monotonic_ns')})
        return row

    def precheck(self,waypoints):
        if not self.enabled:return 'No authorized active phase'
        if (self.root/'STOP').exists():return 'Operator STOP; no further movement'
        if self.deadline_ns and time.monotonic_ns()>=self.deadline_ns:return 'Phase deadline reached'
        actual=self._read_state();offset=np.clip(actual-self.last_sent,-.1,.1)
        previous=self.last_sent
        for index,q in enumerate(waypoints):
            if np.max(np.abs(q[:6]-previous[:6]))>.011:return f'command jump exceeds existing0.011rad limit at waypoint{index+1}'
            for label,v in [('command',q),('predicted measured',q+offset)]:
                reason=self.check.reject(v[:6])
                if reason:return f'{label} waypoint{index+1}/{len(waypoints)}: {reason}'
                lower=self.check.pose(v[:6])['tip_above_table']-.045*abs(K.fk_T(v[:6])[2,1])
                if lower<.006:return f'{label} lower jaw would be {lower:.4f}m above table; min0.006m'
            previous=q
        return None

    def _send(self,cmd):
        if not self.enabled or (self.root/'STOP').exists():raise SafetyAbort('Movement disabled/STOP')
        if self.deadline_ns and time.monotonic_ns()>=self.deadline_ns:raise SafetyAbort('Phase deadline reached')
        before=self._read_state();sent=super()._send(cmd)
        self.journal.emit('sdk_target',env_step=self.num_steps,target=sent.tolist(),measured_before=before.tolist())
        return sent

    def step(self,action):
        if action.meta.get('request_stop') or action.meta.get('observation_only'):
            self.journal.emit('step_without_motion',env_step=self.num_steps,meta=dict(action.meta))
            if action.meta.get('observation_only'):time.sleep(1.05)
            return StepResult(observation=self._observe(self._instruction))
        result=super().step(action)
        self.journal.emit('sdk_readback',env_step=self.num_steps,measured=self._read_state().tolist())
        return result

    def accepted_motion(self,target):
        base=self.last_sent
        moving=bool(np.max(np.abs(target[:6]-base[:6]))>1e-5)
        if self.closed_intent and moving and not self.awaiting_grasp:
            self.attempts+=1;self.awaiting_grasp=True
            self.journal.emit('grasp_attempt',attempt=self.attempts)
        if target[6]<base[6]-.03 and not self.retained:self.closed_intent=True
        if target[6]>base[6]+.03:
            self.closed_intent=False
            if self.retained:
                self.released=True;self.retained=False;self.release_obs=self.obs_seq
                self.journal.emit('release_command',obs_seq=self.obs_seq)


def schema(name,description,properties,required):
    return {'type':'function','function':{'name':name,'description':description,
        'parameters':{'type':'object','properties':properties,'required':required,'additionalProperties':False}}}


class R5Tools:
    def __init__(self,native,rig):self.native,self.rig=native,rig
    def state_labels(self):return self.native.state_labels()
    def residual(self,*a):return self.native.residual(*a)
    def schemas(self):
        note={'type':'string'};number={'type':'number'}
        return self.native.schemas()+[
            schema('measure_pixel','根据腕部当前640x480图像中目标表面像素测量SDK基座坐标；不运动。height为目标表面离桌高度，木块顶面0.034m。必须先从当前图像定位，不能沿用旧像素。',
                   {'u':number,'v':number,'height':number,'note':note},['u','v','note']),
            schema('plan_ee','纯计算：用已验证IK把绝对末端SDK位姿转换为move_joints的targets，不执行运动。返回目标/预测指尖高度。',
                   {**{k:number for k in ('x','y','z','pitch','roll','yaw')},'note':note},['x','y','z','pitch','note']),
            schema('move_ee','以已验证IK规划末端绝对SDK位姿，然后交给原生move_joints安全插值；所有长度米、姿态弧度。',
                   {**{k:number for k in ('x','y','z','pitch','roll','yaw')},'note':note},['x','y','z','pitch','note']),
            schema('move_relative','相对上一次commanded末端目标位姿移动，基准不含实测静差。请对照两种FK高度，不能把实测高度直接减dz当目标高度。',
                   {**{k:number for k in ('dx','dy','dz')},'note':note},['dx','dy','dz','note']),
            schema('observe','只获取新一组双路图片和读回，不发送SDK运动目标，用于放置后的稳定性验证。',{'note':note},['note']),
            schema('verify_grasp','闭爪短抬之后，依据当前两路图判断cube是否随夹爪抬起。不是电流或夹爪数值判断。',{'retained':{'type':'boolean'},'note':note},['retained','note']),
            schema('mark_placement','松爪撤离后，两次图片确认cube完整稳定在蓝盘内；记录A阶段完成，再显式安全回零。',{'earlier_obs':{'type':'integer'},'note':note},['earlier_obs','note'])]

    def anchor_observation(self,obs):
        # Keep the LLM observation truthful; only the motion interpolator uses
        # the last commanded anchor, matching the validated R5 mailbox behavior.
        state=dict(obs.state);state['joint_pos']=self.rig.last_sent.copy()
        return dataclasses.replace(obs,state=state)

    def targets_for_ee(self,args,obs):
        from claude_cmd import ik_targets
        q=self.rig.last_sent;actual=np.asarray(obs.state['joint_pos'])
        sample={'state':dict(zip(self.native._labels,actual)),
                'commanded':dict(zip(self.native._labels,q)),'offset':dict(zip(self.native._labels,actual-q))}
        xyz=[float(args[k]) for k in ('x','y','z')];pitch=float(args['pitch']);roll=float(args.get('roll',0))
        yaw=float(args.get('yaw',math.atan2(xyz[1],xyz[0]+float(K._P0[0]))))
        if not np.isfinite([*xyz,pitch,roll,yaw]).all():raise ValueError('Nonfinite pose')
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):targets=ik_targets(sample,xyz,roll,pitch,yaw)
        if targets is None:raise ValueError(buf.getvalue())
        return targets

    def execute(self,call,obs):
        self.rig.journal.emit('tool_request',call_id=call.id,name=call.name,arguments=call.arguments,obs_seq=self.rig.obs_seq)
        try:result=self._execute(call,obs)
        except (ValueError,KeyError,TypeError) as exc:result=ToolResult(error=str(exc))
        self.rig.journal.emit('tool_result',call_id=call.id,name=call.name,error=result.error,note=result.note,
            steps=len(result.chunk) if result.chunk else 0,target=result.target.tolist() if result.target is not None else None)
        return result

    def _execute(self,call,obs):
        args=json.loads(call.arguments);r=self.rig
        if not isinstance(args,dict):raise ValueError('Expected object arguments')
        if call.name=='observe':
            chunk=ActionChunk([Action(r.last_sent.copy(),meta={'observation_only':True})],control_hz=20)
            return ToolResult(chunk=chunk,note='executing observe over 1 step (0.05s); camera/state refresh only, no SDK target')
        if call.name=='measure_pixel':
            from claude_servo import camera_frame,pixel_to_plane,TABLE_Z
            u,v,h=float(args['u']),float(args['v']),float(args.get('height',.034))
            if not(0<=u<640 and 0<=v<480 and math.isfinite(h)):raise ValueError('Pixel outside current640x480 image or invalid height')
            q=np.asarray(obs.state['joint_pos']);frame=camera_frame(q[:6]);point=pixel_to_plane((u,v),*frame[:4],TABLE_Z+h)
            if point is None:raise ValueError('Ray does not intersect plane')
            return ToolResult(note=json.dumps({'obs_seq':r.obs_seq,'pixel':[u,v],'world_point':point.tolist(),'surface_height_m':h,'measured_tip':frame[4].tolist()},ensure_ascii=False))
        if call.name in ('plan_ee','move_ee','move_relative'):
            if call.name=='move_relative':
                fk=K.fk(r.last_sent[:6]);xyz=fk[:3]+[float(args[k]) for k in ('dx','dy','dz')]
                args={**args,**dict(zip(('x','y','z'),xyz)),'roll':float(fk[3]),'pitch':float(fk[4]),'yaw':float(fk[5])}
            targets=self.targets_for_ee(args,obs)
            target=r.last_sent.copy()
            for key,value in targets.items():target[self.native._labels.index(key)]=value
            if call.name=='plan_ee':
                predicted=target+np.clip(np.asarray(obs.state['joint_pos'])-r.last_sent,-.1,.1)
                return ToolResult(note=json.dumps({'targets':targets,'commanded_geometry':r.check.pose(target[:6]),'predicted_geometry':r.check.pose(predicted[:6])}))
            return self.motion(ToolCall(id=call.id,name='move_joints',arguments=json.dumps({'targets':targets,'note':args['note']})),obs)
        if call.name=='move_joints':return self.motion(call,obs)
        if call.name=='verify_grasp':
            if not r.awaiting_grasp:raise ValueError('No closed-then-lifted attempt to verify')
            if type(args['retained']) is not bool:raise ValueError('retained must be boolean')
            r.retained=args['retained'];r.awaiting_grasp=r.closed_intent=False
            r.journal.emit('grasp_verification',attempt=r.attempts,retained=r.retained,evidence=r.presented[r.obs_seq]['images'],note=args['note'])
            return ToolResult(note='visual grasp judgement recorded')
        if call.name=='mark_placement':
            if r.placement is not None:raise ValueError('Placement already recorded; continue explicit restoration')
            earlier=args['earlier_obs']
            if not r.attempts or not r.released:raise ValueError('Need actual attempted grasp and subsequent release')
            if type(earlier) is not int or earlier not in r.presented or earlier>=r.obs_seq:raise ValueError('Need two distinct observations')
            if earlier<=r.release_obs:raise ValueError('Both placement observations must follow release')
            old,new=r.presented[earlier],r.presented[r.obs_seq]
            if new['monotonic_ns']-old['monotonic_ns']<1_000_000_000:raise ValueError('Observe stability again after one second')
            r.placement=r.journal.emit('A_visual_verification',note=args['note'],attempts=r.attempts,
                evidence={'earlier':old['images'],'latest':new['images']},elapsed_s=(time.monotonic_ns()-r.goal_start['monotonic_ns'])/1e9)
            write_json(r.root/'A_result.json',dict(success_claimed_by_model=True,requires_independent_visual_review=True,**r.placement))
            r.journal.phase='RESTORE';r.deadline_ns=time.monotonic_ns()+1800_000_000_000
            return ToolResult(note='A placement recorded. Now explicitly lift/retract then restore folded joints and close empty gripper before done.')
        if call.name=='done':
            if r.placement is None:return ToolResult(error='Complete and visually verify the cube placement using mark_placement before done')
            actual=np.asarray(obs.state['joint_pos'])
            if np.max(np.abs(actual[:6]))>.1 or r.last_sent[6]>.05:return ToolResult(error='After verified placement, explicitly return to folded posture and close empty gripper before done')
        return self.native.execute(call,self.anchor_observation(obs))

    def motion(self,call,obs):
        r=self.rig
        if r.closed_intent and not r.awaiting_grasp and r.attempts>=2:return ToolResult(error='Maximum2 grasp attempts; give_up without forcing another grasp')
        result=self.native.execute(call,self.anchor_observation(obs))
        if result.chunk is not None and result.target is not None:r.accepted_motion(result.target)
        return result


class AuditedCapture(WireCapture):
    def __init__(self,rig,key):super().__init__();self.rig,self.key=rig,key;self.last_send=None
    def request_started(self,request):
        self.last_send=self.rig.journal.emit('http_request_started',url=str(request.url))
    def record(self,**kwargs):
        if kwargs.get('response_text'):kwargs['response_text']=kwargs['response_text'].replace(self.key,'[REDACTED]')
        super().record(**kwargs)
        data=None
        if kwargs.get('response_text'):
            try:data=json.loads(kwargs['response_text'])
            except ValueError:pass
        self.rig.journal.emit('http_response',attempt=kwargs['attempt'],status=kwargs['status'],
            response_id=data.get('id') if isinstance(data,dict) else None,
            model=data.get('model') if isinstance(data,dict) else None,
            usage=data.get('usage') if isinstance(data,dict) else None,
            stop_reason=data.get('stop_reason') if isinstance(data,dict) else None,
            start=self.last_send,latency_s=(time.monotonic_ns()-self.last_send['monotonic_ns'])/1e9 if self.last_send else None,error=kwargs['error'])
        if self._dead:raise RuntimeError('Required native wire recording failed; no model actions accepted')
        if kwargs['status']==200 and isinstance(data,dict) and data.get('model')!=MODEL:raise RuntimeError('Model identity changed')


class Policy(LLMAgentPolicy):
    def __init__(self,rig,key,notes,transport=None):
        super().__init__(model='anthropic/'+MODEL,wire='messages',effort='medium',max_output_tokens=8192,
            max_llm_calls=100,max_speed_frac=.008,images='always',image_horizon=None,depth='off',
            wire_capture=True,transcript_echo=True,prior_learnings=str(notes),pre_check=rig.precheck,
            env={'ANTHROPIC_API_KEY':key},transport=transport)
        self.rig=rig
        self._capture=AuditedCapture(rig,key);self._client._capture=self._capture
        self._client._http.event_hooks['request'].append(self._capture.request_started)
    def bind(self,info):super().bind(info);self._toolset=R5Tools(self._toolset,self.rig)
    def act(self,obs):
        r=self.rig
        if r.goal_start is None:
            r.snapshot(obs);write_json(r.root/'READY.json',dict(stamp(),status='read_only',observation=str(r.root/'latest.json')))
            while not (r.root/'GO').exists():
                if (r.root/'STOP').exists():raise SafetyAbort('Stopped before model/hardware task')
                time.sleep(.1)
            fresh=r._observe(r._instruction);obs=dataclasses.replace(fresh,extra=obs.extra)
            r.enabled=True;r.deadline_ns=time.monotonic_ns()+1800_000_000_000
            r.goal_start=r.journal.emit('goal_and_initial_observation_received',goal=GOAL,phase_limit_s=1800)
        if (r.root/'STOP').exists() or time.monotonic_ns()>=r.deadline_ns:raise SafetyAbort('STOP or phase deadline')
        row=r.snapshot(obs)
        telemetry={'observation_seq':row['seq'],'phase':r.journal.phase,'remaining_s':round((r.deadline_ns-time.monotonic_ns())/1e9,1),
            'commanded_qpos':row['commanded'],'measured_geometry':row['measured_geometry'],'commanded_geometry':row['commanded_geometry'],
            'grasp_attempts':r.attempts,'awaiting_visual_grasp_check':r.awaiting_grasp}
        obs=dataclasses.replace(obs,instruction=GOAL+'\nR5 telemetry: '+json.dumps(telemetry,ensure_ascii=False))
        chunk=super().act(obs)
        if time.monotonic_ns()>=r.deadline_ns:raise SafetyAbort('Late API result discarded after deadline')
        return chunk


def admin_recovery(rig,policy):
    """Retain the same SDK after an ended eval; only explicit admin commands move."""
    if np.max(np.abs(rig._read_state()[:6]))<.15:return
    rig.enabled=False;rig.journal.phase='ADMIN_RECOVERY';rig.deadline_ns=None
    write_json(rig.root/'holding.json',dict(stamp(),state=rig._read_state().tolist(),same_sdk_recovery_available=True))
    print('HOLDING: API ended; same SDK accepts explicit admin recovery. No automatic folding.',flush=True)
    folder=rig.root/'admin_cmd';folder.mkdir(exist_ok=True);replies=rig.root/'admin_reply';replies.mkdir(exist_ok=True)
    seen=getattr(rig,'admin_seen',set());rig.admin_seen=seen
    while True:
        if (rig.root/'release_after_support').exists():return
        for path in sorted(folder.glob('*.json')):
            if path.name in seen:continue
            seen.add(path.name);cmd=json.loads(path.read_text())
            if cmd.get('op')=='observe':
                obs=rig._observe(rig._instruction);row=rig.snapshot(obs);write_json(replies/path.name,{'ok':True,'observation':row});continue
            if cmd.get('op')=='close':
                folded=np.max(np.abs(rig._read_state()[:6]))<.10
                write_json(replies/path.name,{'ok':bool(folded),'reason':'close only from folded posture'})
                if folded:return
                continue
            if cmd.get('op')!='move_joints':write_json(replies/path.name,{'ok':False,'error':'Unsupported administrative op'});continue
            if (rig.root/'STOP').exists():write_json(replies/path.name,{'ok':False,'error':'STOP must remain respected; no recovery targets sent'});continue
            rig.enabled=True;obs=rig._observe(rig._instruction)
            call=ToolCall(id='admin-'+path.stem,name='move_joints',arguments=json.dumps({'targets':cmd['targets'],'note':cmd['note']}))
            result=policy._toolset.native.execute(call,policy._toolset.anchor_observation(obs))
            if result.chunk:
                for action in result.chunk.actions:rig.step(action)
            row=rig.snapshot(rig._observe(rig._instruction));rig.enabled=False
            write_json(replies/path.name,{'ok':result.error is None,'error':result.error,'note':result.note,'observation':row})
        time.sleep(.1)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--notes',type=Path,default=HERE/'opus55_r5_protocol.md');parser.add_argument('--fake',action='store_true')
    parser.add_argument('--safety-confirmation',type=Path);args=parser.parse_args()
    if not args.fake:
        c=json.loads(args.safety_confirmation.read_text()) if args.safety_confirmation else {}
        if c.get('confirmed') is not True or c.get('model')!=MODEL:raise ValueError('Current-session Opus physical safety confirmation required')
    root=args.run_dir.resolve();root.mkdir(parents=True,exist_ok=False)
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    owner=open('/tmp/r5-opus-fake.lock' if args.fake else '/tmp/r5-benchmark-can1.lock','w');fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
    key='fake-key-no-network' if args.fake else (Path.home()/'.config/r5-benchmark/anthropic_api_key').read_text().strip()
    journal=Journal(root)
    d=load_defaults(os.environ);cfg=ArxR5Config.from_kwargs(**d.embodiment_args).replace(unattended=True,park_before_grade=False,auto_start=False)
    if (cfg.arms,cfg.left_can,cfg.arm_type)!=('left','can1',0):raise ValueError('Only left follower can1/type0')
    saved_out,saved_err=os.dup(1),os.dup(2);fd=os.open(root/'sdk.log',os.O_WRONLY|os.O_CREAT,0o600)
    os.dup2(fd,1);os.dup2(fd,2);os.close(fd)
    sys.stdout=os.fdopen(saved_out,'w',buffering=1);sys.stderr=os.fdopen(saved_err,'w',buffering=1)
    rig=Rig(cfg,root,journal,args.fake)
    transport=None
    if args.fake:
        from test_opus_r5 import fake_transport
        transport=fake_transport(rig);(root/'GO').touch()
    policy=Policy(rig,key,args.notes,transport=transport)
    frozen=root/'frozen';frozen.mkdir()
    sources=[Path(__file__),args.notes,HERE/'inspect_codex_run.py',HERE/'claude_cmd.py',HERE/'claude_servo.py',HERE/'r5_kin.py',HERE/'inspect_r5.ini',HERE/'inspect_env.sh',HERE/'inspect_claude_driver/src/inspect_claude_driver/policy.py']
    for i,p in enumerate(sources):shutil.copy2(p,frozen/f'{i:02d}_{p.name}')
    versions={name:importlib.metadata.version(name) for name in ('inspect-robots','inspect-robots-agent','inspect-robots-arx-r5','numpy','httpx')}
    versions['framework_commit']=subprocess.check_output(['git','-C',str(HERE/'inspect-robots'),'rev-parse','HEAD'],text=True).strip()
    versions['framework_dirty']=subprocess.check_output(['git','-C',str(HERE/'inspect-robots'),'status','--porcelain'],text=True)
    versions['python']=sys.version
    write_json(root/'versions.json',versions)
    write_json(root/'run.json',dict(stamp(),pid=os.getpid(),fake=args.fake,model=MODEL,policy='agent',
        cfg=dataclasses.asdict(cfg),task='single A plus explicitly separated safe restoration',phase_limit_s=1800,
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        customizations=['R5 math/visual-checkpoint tools','commanded interpolation anchor; real measured state remains in model observation','existing target/predicted geometry precheck','no-motion stop and explicit observation refresh','same-owner administrative recovery after ended eval']))
    task=Task(name='r5-opus55-api-cube-to-plate',scenes=[Scene(id=root.name,instruction=GOAL)],scorer=episode_length(),max_steps=12000)
    code=1
    try:
        space=rig.info.action_space
        (log,)=ir_eval(task,policy,rig,log_dir=str(root/'eval'),
             approver=ChainApprover(ClampApprover(space),DeltaLimitApprover(space)),store_frames=True,store_actions=True)
        journal.emit('framework_end',status=log.status,error=str(log.error));code=0 if log.status=='success' else 1
    except BaseException as exc:
        journal.emit('runner_stopped',error=f'{type(exc).__name__}: {exc}')
    finally:
        rig.enabled=False
        if rig._drivers is not None:
            if not args.fake:
                while True:
                    try:admin_recovery(rig,policy);break
                    except BaseException as exc:
                        rig.enabled=False
                        journal.emit('admin_recovery_error_holding',error=f'{type(exc).__name__}: {exc}')
                        print('Still holding; inspect error and send a new explicit recovery command.',flush=True)
                        time.sleep(.2)
            rig.close()
        policy._client.close();journal.emit('sdk_released');journal.fp.close()
        print('SDK_RELEASED',flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
