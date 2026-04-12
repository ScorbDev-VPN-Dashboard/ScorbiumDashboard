"""
RemnaWave API client — production-ready implementation.
Based on official RemnaWave API with retry logic, proper parsing and full endpoint support.
"""
import asyncio
import base64
import json
import ssl
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import aiohttp

from app.utils.log import log


class UserStatus(Enum):
    ACTIVE = 'ACTIVE'
    DISABLED = 'DISABLED'
    LIMITED = 'LIMITED'
    EXPIRED = 'EXPIRED'


class TrafficLimitStrategy(Enum):
    NO_RESET = 'NO_RESET'
    DAY = 'DAY'
    WEEK = 'WEEK'
    MONTH = 'MONTH'
    MONTH_ROLLING = 'MONTH_ROLLING'


@dataclass
class UserTraffic:
    used_traffic_bytes: int
    lifetime_used_traffic_bytes: int
    online_at: datetime | None = None
    first_connected_at: datetime | None = None
    last_connected_node_uuid: str | None = None


@dataclass
class RemnaWaveUser:
    uuid: str
    short_uuid: str
    username: str
    status: UserStatus
    traffic_limit_bytes: int
    traffic_limit_strategy: TrafficLimitStrategy
    expire_at: datetime
    telegram_id: int | None
    email: str | None
    hwid_device_limit: int | None
    description: str | None
    tag: str | None
    subscription_url: str
    active_internal_squads: list[dict[str, str]]
    created_at: datetime
    updated_at: datetime
    user_traffic: UserTraffic | None = None
    sub_revoked_at: datetime | None = None
    last_traffic_reset_at: datetime | None = None
    trojan_password: str | None = None
    vless_uuid: str | None = None
    ss_password: str | None = None
    last_triggered_threshold: int = 0
    happ_link: str | None = None
    happ_crypto_link: str | None = None
    external_squad_uuid: str | None = None
    id: int | None = None

    @property
    def used_traffic_bytes(self) -> int:
        if self.user_traffic:
            return self.user_traffic.used_traffic_bytes
        return 0

    @property
    def lifetime_used_traffic_bytes(self) -> int:
        if self.user_traffic:
            return self.user_traffic.lifetime_used_traffic_bytes
        return 0

    @property
    def online_at(self) -> datetime | None:
        if self.user_traffic:
            return self.user_traffic.online_at
        return None


@dataclass
class RemnaWaveNode:
    uuid: str
    name: str
    address: str
    country_code: str
    is_connected: bool
    is_disabled: bool
    users_online: int
    traffic_used_bytes: int | None
    traffic_limit_bytes: int | None
    port: int | None = None
    is_connecting: bool = False
    view_position: int = 0
    tags: list[str] | None = None
    last_status_change: datetime | None = None
    last_status_message: str | None = None
    xray_uptime: int = 0
    is_traffic_tracking_active: bool = False
    traffic_reset_day: int | None = None
    notify_percent: int | None = None
    consumption_multiplier: float = 1.0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    provider_uuid: str | None = None
    versions: dict[str, str] | None = None
    system: dict[str, Any] | None = None
    active_plugin_uuid: str | None = None

    @property
    def is_node_online(self) -> bool:
        return self.is_connected


class RemnaWaveAPIError(Exception):
    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        self.message = message
        self.status_code = status_code
        self.response_data = response_data
        super().__init__(self.message)


class RemnaWaveAPI:
    def __init__(
        self,
        base_url: str,
        api_key: str = None,
        username: str = None,
        password: str = None,
        auth_type: str = 'api_key',
    ):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.username = username
        self.password = password
        self.auth_type = auth_type.lower() if auth_type else 'api_key'
        self.session: aiohttp.ClientSession | None = None
        self._jwt_token: str | None = None
        self._jwt_expires: datetime | None = None

    def _detect_connection_type(self) -> str:
        parsed = urlparse(self.base_url)
        local_hosts = ['localhost', '127.0.0.1', 'remnawave', 'remnawave-backend', 'app', 'api']
        if parsed.hostname in local_hosts:
            return 'local'
        if parsed.hostname:
            if (parsed.hostname.startswith('192.168.')
                    or parsed.hostname.startswith('10.')
                    or parsed.hostname.startswith('172.')
                    or parsed.hostname.endswith('.local')):
                return 'local'
        return 'external'

    def _prepare_auth_headers(self) -> dict[str, str]:
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        elif self._jwt_token:
            headers['Authorization'] = f'Bearer {self._jwt_token}'
        return headers

    async def _ensure_jwt(self) -> None:
        """Получаем JWT токен через login если нет API key."""
        if self.api_key:
            return
        now = datetime.now()
        if self._jwt_token and self._jwt_expires and now < self._jwt_expires:
            return
        if not self.username or not self.password:
            raise RemnaWaveAPIError("Remnawave: нужен api_key или username+password")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f'{self.base_url}/api/auth/login',
                json={'username': self.username, 'password': self.password},
                ssl=False,
            ) as resp:
                if resp.status not in (200, 201):
                    raise RemnaWaveAPIError(f"Auth failed: {resp.status}")
                data = await resp.json()
                token_data = data.get('response', data)
                self._jwt_token = token_data.get('accessToken') or token_data.get('access_token')
                if not self._jwt_token:
                    raise RemnaWaveAPIError(f"No accessToken in response: {data}")
                from datetime import timedelta
                self._jwt_expires = now + timedelta(hours=23)
                log.info("✅ Remnawave JWT token refreshed")

    async def __aenter__(self):
        await self._ensure_jwt()
        conn_type = self._detect_connection_type()
        headers = self._prepare_auth_headers()
        connector_kwargs = {}
        if conn_type == 'local' and self.base_url.startswith('https://'):
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connector_kwargs['ssl'] = ssl_context
        connector = aiohttp.TCPConnector(**connector_kwargs)
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60, connect=10),
            headers=headers,
            connector=connector,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_request(self, method: str, endpoint: str, data: dict | None = None, params: dict | None = None) -> dict:
        if not self.session:
            raise RemnaWaveAPIError('Session not initialized. Use async context manager.')
        url = f'{self.base_url}{endpoint}'
        max_retries = 3
        base_delay = 1.0
        for attempt in range(max_retries + 1):
            try:
                kwargs = {'url': url, 'params': params}
                if data:
                    kwargs['json'] = data
                async with self.session.request(method, **kwargs) as response:
                    response_text = await response.text()
                    try:
                        response_data = json.loads(response_text) if response_text else {}
                    except json.JSONDecodeError:
                        response_data = {'raw_response': response_text}

                    if response.status in (429, 502, 503, 504) and attempt < max_retries:
                        retry_after = float(response.headers.get('Retry-After', base_delay * (2 ** attempt)))
                        await asyncio.sleep(retry_after)
                        continue

                    if response.status >= 400:
                        error_message = response_data.get('message', f'HTTP {response.status}')
                        raise RemnaWaveAPIError(error_message, response.status, response_data)

                    return response_data

            except aiohttp.ClientError as e:
                if attempt < max_retries:
                    await asyncio.sleep(base_delay * (2 ** attempt))
                    continue
                raise RemnaWaveAPIError(f'Request failed: {e!s}')

        raise RemnaWaveAPIError(f'Max retries exceeded for {method} {endpoint}')

    # ── Users ────────────────────────────────────────────────────────────────

    async def create_user(
        self,
        username: str,
        expire_at: datetime,
        status: UserStatus = UserStatus.ACTIVE,
        traffic_limit_bytes: int = 0,
        traffic_limit_strategy: TrafficLimitStrategy = TrafficLimitStrategy.NO_RESET,
        telegram_id: int | None = None,
        **kwargs,
    ) -> RemnaWaveUser:
        data = {
            'username': username,
            'status': status.value,
            'expireAt': expire_at.isoformat(),
            'trafficLimitBytes': traffic_limit_bytes,
            'trafficLimitStrategy': traffic_limit_strategy.value,
        }
        if telegram_id:
            data['telegramId'] = telegram_id
        response = await self._make_request('POST', '/api/users', data)
        return self._parse_user(response['response'])

    async def get_user_by_uuid(self, uuid: str) -> RemnaWaveUser | None:
        try:
            response = await self._make_request('GET', f'/api/users/{uuid}')
            return self._parse_user(response['response'])
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_user_by_username(self, username: str) -> RemnaWaveUser | None:
        try:
            response = await self._make_request('GET', f'/api/users/by-username/{username}')
            return self._parse_user(response['response'])
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return None
            raise

    async def get_user_by_telegram_id(self, telegram_id: int) -> list[RemnaWaveUser]:
        try:
            response = await self._make_request('GET', f'/api/users/by-telegram-id/{telegram_id}')
            users_data = response.get('response', [])
            return [self._parse_user(u) for u in users_data]
        except RemnaWaveAPIError as e:
            if e.status_code == 404:
                return []
            raise

    async def update_user(self, uuid: str, **fields) -> RemnaWaveUser:
        data = {'uuid': uuid, **fields}
        response = await self._make_request('PATCH', '/api/users', data)
        return self._parse_user(response['response'])

    async def delete_user(self, uuid: str) -> bool:
        response = await self._make_request('DELETE', f'/api/users/{uuid}')
        return response['response']['isDeleted']

    async def enable_user(self, uuid: str) -> RemnaWaveUser:
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/enable')
        return self._parse_user(response['response'])

    async def disable_user(self, uuid: str) -> RemnaWaveUser:
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/disable')
        return self._parse_user(response['response'])

    async def reset_user_traffic(self, uuid: str) -> RemnaWaveUser:
        response = await self._make_request('POST', f'/api/users/{uuid}/actions/reset-traffic')
        return self._parse_user(response['response'])

    async def get_all_users(self, start: int = 0, size: int = 100) -> dict[str, Any]:
        params = {'start': start, 'size': size}
        response = await self._make_request('GET', '/api/users', params=params)
        users = [self._parse_user(u) for u in response['response']['users']]
        return {'users': users, 'total': response['response']['total']}

    # ── Nodes ────────────────────────────────────────────────────────────────

    async def get_all_nodes(self) -> list[RemnaWaveNode]:
        response = await self._make_request('GET', '/api/nodes')
        return [self._parse_node(n) for n in response['response']]

    # ── System ───────────────────────────────────────────────────────────────

    async def get_system_stats(self) -> dict[str, Any]:
        response = await self._make_request('GET', '/api/system/stats')
        return response['response']

    async def get_bandwidth_stats(self) -> dict[str, Any]:
        response = await self._make_request('GET', '/api/system/stats/bandwidth')
        return response['response']

    # ── Subscription ─────────────────────────────────────────────────────────

    async def get_subscription_info(self, short_uuid: str) -> dict[str, Any]:
        response = await self._make_request('GET', f'/api/sub/{short_uuid}/info')
        return response['response']

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_user_traffic(self, traffic_data: dict | None) -> UserTraffic | None:
        if not traffic_data:
            return None
        return UserTraffic(
            used_traffic_bytes=int(traffic_data.get('usedTrafficBytes', 0)),
            lifetime_used_traffic_bytes=int(traffic_data.get('lifetimeUsedTrafficBytes', 0)),
            online_at=self._parse_dt(traffic_data.get('onlineAt')),
            first_connected_at=self._parse_dt(traffic_data.get('firstConnectedAt')),
            last_connected_node_uuid=traffic_data.get('lastConnectedNodeUuid'),
        )

    def _parse_user(self, d: dict) -> RemnaWaveUser:
        status_str = d.get('status') or 'ACTIVE'
        try:
            status = UserStatus(status_str)
        except ValueError:
            status = UserStatus.ACTIVE

        strategy_str = d.get('trafficLimitStrategy') or 'NO_RESET'
        try:
            strategy = TrafficLimitStrategy(strategy_str)
        except ValueError:
            strategy = TrafficLimitStrategy.NO_RESET

        return RemnaWaveUser(
            uuid=d['uuid'],
            short_uuid=d['shortUuid'],
            username=d['username'],
            status=status,
            traffic_limit_bytes=d.get('trafficLimitBytes', 0),
            traffic_limit_strategy=strategy,
            expire_at=datetime.fromisoformat(d['expireAt'].replace('Z', '+00:00')),
            telegram_id=d.get('telegramId'),
            email=d.get('email'),
            hwid_device_limit=d.get('hwidDeviceLimit'),
            description=d.get('description'),
            tag=d.get('tag'),
            subscription_url=d.get('subscriptionUrl', ''),
            active_internal_squads=d.get('activeInternalSquads', []),
            created_at=datetime.fromisoformat(d['createdAt'].replace('Z', '+00:00')),
            updated_at=datetime.fromisoformat(d['updatedAt'].replace('Z', '+00:00')),
            user_traffic=self._parse_user_traffic(d.get('userTraffic')),
            sub_revoked_at=self._parse_dt(d.get('subRevokedAt')),
            last_traffic_reset_at=self._parse_dt(d.get('lastTrafficResetAt')),
            trojan_password=d.get('trojanPassword'),
            vless_uuid=d.get('vlessUuid'),
            ss_password=d.get('ssPassword'),
            last_triggered_threshold=d.get('lastTriggeredThreshold', 0),
            external_squad_uuid=d.get('externalSquadUuid'),
            id=d.get('id'),
        )

    def _parse_node(self, d: dict) -> RemnaWaveNode:
        return RemnaWaveNode(
            uuid=d['uuid'],
            name=d['name'],
            address=d['address'],
            country_code=d.get('countryCode', ''),
            is_connected=d.get('isConnected', False),
            is_disabled=d.get('isDisabled', False),
            users_online=d.get('usersOnline', 0),
            traffic_used_bytes=d.get('trafficUsedBytes'),
            traffic_limit_bytes=d.get('trafficLimitBytes'),
            port=d.get('port'),
            is_connecting=d.get('isConnecting', False),
            view_position=d.get('viewPosition', 0),
            tags=d.get('tags', []),
            last_status_change=self._parse_dt(d.get('lastStatusChange')),
            last_status_message=d.get('lastStatusMessage'),
            xray_uptime=int(d.get('xrayUptime') or 0),
            is_traffic_tracking_active=d.get('isTrafficTrackingActive', False),
            traffic_reset_day=d.get('trafficResetDay'),
            notify_percent=d.get('notifyPercent'),
            consumption_multiplier=d.get('consumptionMultiplier', 1.0),
            created_at=self._parse_dt(d.get('createdAt')),
            updated_at=self._parse_dt(d.get('updatedAt')),
            provider_uuid=d.get('providerUuid'),
            versions=d.get('versions'),
            system=d.get('system'),
            active_plugin_uuid=d.get('activePluginUuid'),
        )

    def _parse_dt(self, s: str | None) -> datetime | None:
        if s:
            return datetime.fromisoformat(s.replace('Z', '+00:00'))
        return None


def get_remnawave_api() -> RemnaWaveAPI:
    """Фабрика — создаёт RemnaWaveAPI из конфига."""
    from app.core.configs.remnawave_config import remnawave as _cfg
    if not _cfg or not _cfg.remnawave_url:
        raise RuntimeError("Remnawave not configured. Set REMNAWAVE_URL in .env")

    api_key = _cfg.remnawave_api_key.get_secret_value() if _cfg.remnawave_api_key else None
    login = _cfg.remnawave_login if not api_key else None
    password = _cfg.remnawave_password.get_secret_value() if (not api_key and _cfg.remnawave_password) else None

    if not api_key and not (login and password):
        raise RuntimeError("Remnawave: задайте REMNAWAVE_API_KEY или REMNAWAVE_LOGIN + REMNAWAVE_PASSWORD")

    return RemnaWaveAPI(
        base_url=_cfg.remnawave_url,
        api_key=api_key,
        username=login,
        password=password,
    )
