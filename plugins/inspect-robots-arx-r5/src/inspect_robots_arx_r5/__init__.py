"""inspect-robots-arx-r5: ARX (方舟无限) R5 arms as an Inspect Robots embodiment.

Registers the ``arx_r5`` embodiment through the ``inspect_robots.embodiments``
entry point, so once installed::

    inspect-robots list embodiments            # -> includes "arx_r5"
    inspect-robots "pick up the cube" --policy agent --embodiment arx_r5 \\
        -E sdk_path=/path/to/R5/py/ARX_R5_python \\
        -E cameras=top:/dev/video0,wrist:/dev/video2

Programmatic use::

    from inspect_robots import eval
    from inspect_robots_arx_r5 import ArxR5Config, ArxR5Embodiment

    emb = ArxR5Embodiment(ArxR5Config(arms="left", left_can="can1"))
    eval("cubepick-reach", "agent", emb)
"""

from __future__ import annotations

from typing import Any

from inspect_robots_arx_r5.config import ArxR5Config
from inspect_robots_arx_r5.embodiment import ArxR5Embodiment

__all__ = ["ArxR5Config", "ArxR5Embodiment", "arx_r5_embodiment"]

__version__ = "0.1.0"


def arx_r5_embodiment(**kwargs: Any) -> ArxR5Embodiment:
    """Factory the Inspect Robots registry calls (entry point ``arx_r5``).

    Accepts the same keyword arguments as :class:`ArxR5Embodiment`; the CLI
    forwards ``-E key=value`` pairs and ``[embodiment.args]`` here.
    """
    return ArxR5Embodiment(**kwargs)


# The setup wizard and `inspect-robots doctor` read these off the factory.
arx_r5_embodiment.DEVICE_SLOTS = ArxR5Embodiment.DEVICE_SLOTS  # type: ignore[attr-defined]
arx_r5_embodiment.OPTION_SLOTS = ArxR5Embodiment.OPTION_SLOTS  # type: ignore[attr-defined]
arx_r5_embodiment.NUMBER_SLOTS = ArxR5Embodiment.NUMBER_SLOTS  # type: ignore[attr-defined]
arx_r5_embodiment.RUNTIME_REQUIREMENTS = ArxR5Embodiment.RUNTIME_REQUIREMENTS  # type: ignore[attr-defined]
