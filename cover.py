import logging

from homeassistant.components.cover import CoverEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import ConfigType, entity_platform

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: entity_platform.AddEntitiesCallback,
    discovery_info=None,
) -> None:
    """Set up the Ryobi garage door openers."""
    garage_devices = []
    for device in hass.data[DOMAIN]:
        garage_devices.append(RyobiGarageDoor(device))
        hass.loop.create_task(device.watch_state())

    _LOGGER.debug("Adding Ryobi Garage Door to Home Assistant: %s", garage_devices)
    async_add_entities(garage_devices, False)


class RyobiGarageDoor(CoverEntity):
    """
    Ryobi Garage Door Opener
    """

    hi = "hi"
