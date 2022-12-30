import asyncio
import json
import logging
import threading

import httpx
import websocket

_LOGGER = logging.getLogger(__name__)

# Socket
SOCK_CONNECTED = "Open"
SOCK_CLOSE = "Close"
SOCK_ERROR = "Error"
# API Answer
SUCCESS_OK = "success"
SERVICE_ERROR = "ServiceErrorException"
USER_NOT_EXIST = "UserNotExist"
PASSWORD_NOK = "PasswordInvalid"

# API
RYOBI_URL = "tti.tiwiconnect.com"
HTTP_URL = f"https://{RYOBI_URL}"
WS_URL = f"wss://{RYOBI_URL}"
HTTP_ENDPOINT = f"{HTTP_URL}/api"
WS_ENDPOINT = f"{WS_URL}/api/wsrpc"
N_RETRY = 6
ACK_TIMEOUT = 5
HTTP_TIMEOUT = 5


class RyobiApi:
    """
    Ryobi API
    Handle connexion with Ryobi's server to get WSS credentials
    """

    def __init__(self, username, password):
        _LOGGER.debug("RyobiApi __init__")

        self.username = username
        self.password = password

        self.api_key = None
        self.user_id = None
        self.devices = []

    async def login(self, url=f"{HTTP_ENDPOINT}/login") -> bool:
        """ "
        Login to Ryobi platform
        """
        _LOGGER.debug("RyobiApi try login")

        params = {"username": self.username, "password": self.password}

        headers = {
            "host": RYOBI_URL,
            "content-type": "application/json",
        }

        resp = await self.send_http(url, params, headers)

        if "_id" in resp["result"]:
            self.user_id = resp["result"]["_id"]

            if "apikey" in resp["result"]["auth"]:
                self.api_key = resp["result"]["auth"]["apiKey"]
            else:
                _LOGGER.error(
                    "RyobiApi: Could not get Api Key from server response: %s", resp
                )
                return False

            _LOGGER.debug("RyobiApi: Login successful. Id and Api Key retrieved")
            return True
        else:
            _LOGGER.error(
                "RyobiApi: Something went wrong with login, no user id found. See server response: %s",
                resp,
            )
            return False

    async def get_devices(self, url=f"{HTTP_ENDPOINT}/devices"):
        """
        Get device list registered from Ryobi server
        """
        _LOGGER.debug("RyobiApi get list of devices")

        params = {"username": self.username, "password": self.password}

        headers = {
            "host": RYOBI_URL,
            "content-type": "application/json",
        }

        resp = await self.send_http(url, params, headers, "GET")

        if resp:  # list not empty
            for result in resp["result"]:
                device = {}
                device["username"] = self.username
                device["password"] = self.password
                device["api_key"] = self.api_key
                device["u_id"] = self.user_id

                device["type_ids"] = result["deviceTypeIds"]
                device["d_id"] = result["varName"]
                device["name"] = result["metaData"]["name"]
                device["version"] = result["metaData"]["version"]
                device["description"] = result["metaData"]["description"]
                device["last_seen"] = result["metaData"]["sys"]["lastSeen"]
                self.devices.append(device)

            _LOGGER.debug(
                "RyobiApi: Get device list OK. %s devices found", len(self.devices)
            )
            return self.devices
        else:
            _LOGGER.error("RyobiApi: FAILED to get device list: %s", resp)
            return []

    @staticmethod
    async def send_http(url, params, headers, method="POST"):
        """
        Send HTTP request
        """
        _LOGGER.debug("Send HTTP request Url=%s Params=%s", url, params)
        timeout = httpx.Timeout(HTTP_TIMEOUT, connect=15.0)
        request = httpx.Request(method, url, params=params, headers=headers)
        for attempt in range(N_RETRY):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.send(request)
                    if resp.status_code == 200:
                        # Server status OK
                        _LOGGER.debug("RyobiApi: Send HTTP OK, return=200")
                        _LOGGER.debug("RyobiApi: HTTP data received = %s", resp.json())
                        return resp.json()
                    elif resp.status_code == 401:
                        _LOGGER.error(
                            "RyobiApi: Invlaid login credentials. HTTP 401 Unauthorized. Skipping retry"
                        )
                        return {
                            "msg": "error",
                            "details": "Invalid login credentials HTTP 401 Unothorized. Skipped retry",
                        }
                    else:
                        # Server status NOK
                        _LOGGER.warning(
                            "RyobiApi: Bad server response (status code=%s) retry... (%s/%s)",
                            resp.status_code,
                            attempt,
                            N_RETRY,
                        )
            except httpx.RequestError as err:
                _LOGGER.debug(
                    "Send HTTP exception details=%s retry... (%s/%s)",
                    err,
                    attempt,
                    N_RETRY,
                )

        _LOGGER.error("RyobiApi: HTTP error after %s retry", N_RETRY)
        return {"msg": "error", "details": f"Failed after {N_RETRY} retry"}


class RyobiWssCtrl(RyobiApi):
    """
    Ryobi Websocket Controller
    Handle websocket to send/recieve garage door control and information
    """

    def __init__(self, username, password, api_key, u_id, d_id):
        super().__init__(username, password)
        _LOGGER.debug("RyobiApi WSS Control __init__")

        self.socket_state = SOCK_CLOSE
        self.device_status = None
        self.subscriber = []
        self.wst = None
        self.ws = None
        self._refresh_time = 60
        self.sent_counter = 0

        self.api_key = api_key

    async def check_credentials(self):
        """
        Check if credentials for WSS are OK
        """
        _LOGGER.debug("RyobiApi (WSS) Checking credentials")
        if not self.api_key:
            _LOGGER.debug("RyobiApi (WSS) api key needed")
            if await self.login():
                return True
            else:
                return False
        _LOGGER.debug("RyobiApi (WSS) api_key are OK")
        return True

    async def open_wss_thread(self):
        """
        Connect WebSocket to Ryobi Server and create a thread to maintain connection alive
        """
        if not await self.check_credentials():
            _LOGGER.error("RyobiApi (WSS) Failed to obtain WSS Api Key")
            return False

        _LOGGER.debug("RyobiApi (WSS) Addr=%s / Api Key=%s", WS_ENDPOINT, self.api_key)

        try:
            self.ws = websocket.WebSocketApp(
                WS_ENDPOINT,
                header={
                    "Connection": "keep-alive, Upgrade",
                    "handshakeTimeout": "10000",
                },
                on_message=self.on_message,
                on_close=self.on_close,
                on_open=self.on_open,
                on_error=self.on_error,
                on_pong=self.on_pong,
            )

            self.authenticate()

            self.subscribe()

            self.wst = threading.Thread(target=self.ws.run_forever)
            self.wst.start()

            if self.wst.is_alive():
                _LOGGER.debug("RyobiApi (WSS) Thread was init")
                return True
            else:
                _LOGGER.error("RyobiApi (WSS) Thread connection init has FAILED")
                return False

        except websocket.WebSocketException as err:
            self.socket_state = SOCK_ERROR
            _LOGGER.debug("RyobiApi (WSS) Error while opening socket: %s", err)
            return False

    async def connect_wss(self):
        """Connect to websocket"""
        if self.socket_state == SOCK_CONNECTED:
            return True

        _LOGGER.debug("RyobiApi (WSS) Not connected, connecting")

        if await self.open_wss_thread():
            _LOGGER.debug("RyobiApi (WSS) Connecting")
        else:
            return False

        for i in range(15):
            _LOGGER.debug("RyobiApi (WSS) awaiting connection established... %s", i)
            if self.socket_state == SOCK_CONNECTED:
                return True
            await asyncio.sleep(0.5)
        return False

    def on_error(self, ws, error):
        """Socket "On_Error" event"""
        details = ""
        if error:
            details = f"(details : {error})"
        _LOGGER.debug("RyobiApi (WSS) Error: %s", details)
        self.socket_state = SOCK_ERROR

    def on_close(self, ws, close_status_code, close_msg):
        """Socket "On_Close" event"""
        _LOGGER.debug("RyobiApi (WSS) Closed")

        if close_status_code or close_msg:
            _LOGGER.debug(
                "RyobiApi (WSS) Close Status_code: %s", str(close_status_code)
            )
            _LOGGER.debug("RyobiApi (WSS) Close Message: %s", str(close_msg))
        self.socket_state = SOCK_CLOSE

    def on_pong(self, message):
        """Socket on_pong event"""
        _LOGGER.debug("RyobiApi (WSS) Got a Pong")

    def on_open(self, ws):
        """Socket "On_Open" event"""
        _LOGGER.debug("RyobiApi (WSS) Connexion established OK")
        self.socket_state = SOCK_CONNECTED

    def on_message(self, ws, message):
        """Socket "On_Message" event"""
        self.sent_counter = 0
        wss_data = json.loads(message)
        _LOGGER.debug("RyobiApi (WSS) Msg received %s", wss_data)

        # TODO deal with incoming messages and updates

    async def publish_wss(self, dict_message):
        """
        Publish payload over WSS connexion
        """
        json_message = json.dumps(dict_message)
        _LOGGER.debug("RyobiApi (WSS) Publishing message : %s", json_message)

        if self.sent_counter >= 5:
            _LOGGER.warning(
                "RyobiApi (WSS) Link is UP, but server has stopped answering request. "
            )
            self.sent_counter = 0
            self.ws.close()
            self.socket_state = SOCK_CLOSE

        for attempt in range(N_RETRY):
            if self.socket_state == SOCK_CONNECTED:
                try:
                    self.ws.send(json_message)
                    self.sent_counter += 1
                    _LOGGER.debug("RyobiApi (WSS) Msg published OK (%s)", attempt)
                    return True
                except websocket.WebSocketConnectionClosedException as err:
                    self.socket_state = SOCK_CLOSE
                    _LOGGER.debug(
                        "RyobiApi (WSS) Error while publishing message (details: %s)",
                        err,
                    )
            else:
                _LOGGER.debug(
                    "RyobiApi (WSS) Can't publish message socket_state= %s, reconnecting... ",
                    self.socket_state,
                )
                await self.connect_wss()

        _LOGGER.error(
            "RyobiApi (WSS) Failed to puslish message after %s retry. ", N_RETRY
        )
        return False
