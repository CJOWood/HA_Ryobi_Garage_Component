import logging

from .ryobiapi import RyobiWssCtrl

_LOGGER = logging.getLogger(__name__)


class GdoDevice(RyobiWssCtrl):
    """
    A representation of the Ryobi Device to listen to WS.
    """

    def __init__(
        self,
        username,
        password,
        api_key,
        u_id,
        d_id,
        name,
        description,
        version,
        type_ids,
        last_seen,
        status=None,
    ) -> None:
        _LOGGER.debug("Ryobi GDODevice __init__")
        super().__init__(username, password, api_key, u_id, d_id)

        self.username = username
        self.password = password
        self.api_key = api_key
        self.user_id = u_id

        self.type_ids = type_ids
        self.device_id = d_id
        self.name = name
        self.version = version
        self.description = description
        self.last_seen = last_seen

        # if self.status is None: #from https://github.com/Jezza34000/homeassistant_weback_component/blob/bc2ac510515392e8f22c6a74abc99f7442084722/custom_components/weback_vacuum/VacDevice.py#L18
        self.status = status

    async def watch_state(self):
        """
        State watcher from VacDevice
        """
        _LOGGER.debug(
            "GdoDevice: starting state watcher for= %s %s", self.name, self.device_id
        )
        try:
            await self.refresh_handler(self.device_id)
        except:
            _LOGGER.exception("Error on watch_state starting refresh_handler")
