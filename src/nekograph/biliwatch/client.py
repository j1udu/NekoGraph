"""Asynchronous Bilibili HTTP boundary and response normalization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from nekograph.biliwatch.config import BiliWatchConfigStore
from nekograph.biliwatch.models import BiliDynamic, BiliLiveStatus


class BilibiliAPIError(RuntimeError):
    """A Bilibili endpoint failed or returned an application error."""


class BilibiliClient:
    def __init__(
        self,
        config: BiliWatchConfigStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15,
    ) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def recent_dynamics(self, uid: str) -> list[BiliDynamic]:
        payload = await self._get(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
            params={"host_mid": uid, "offset": ""},
        )
        raw_items: object = _mapping(payload.get("data")).get("items", [])
        if not isinstance(raw_items, list):
            raise BilibiliAPIError("Bilibili dynamic response has invalid items")
        items = cast(list[object], raw_items)
        dynamics: list[BiliDynamic] = []
        for value in items:
            if not isinstance(value, dict):
                continue
            parsed = parse_dynamic(cast(dict[str, Any], value), uid)
            if parsed is not None:
                dynamics.append(parsed)
        return dynamics

    async def dynamic_detail(self, dynamic_id: str, uid: str) -> BiliDynamic | None:
        try:
            payload = await self._get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail",
                params={"id": dynamic_id},
            )
            item = _mapping(payload.get("data")).get("item")
            if isinstance(item, dict):
                return parse_dynamic(cast(dict[str, Any], item), uid)
        except BilibiliAPIError:
            pass
        try:
            payload = await self._get(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
                params={"dynamic_id": dynamic_id},
            )
            card = _mapping(payload.get("data")).get("card")
            if isinstance(card, str):
                try:
                    card = json.loads(card)
                except json.JSONDecodeError:
                    card = {}
            if isinstance(card, dict):
                return _parse_legacy_dynamic(cast(dict[str, Any], card), dynamic_id, uid)
        except BilibiliAPIError:
            pass
        return None

    async def live_status(self, uid: str) -> BiliLiveStatus:
        room_payload = await self._get(
            "https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld",
            params={"mid": uid},
        )
        room = _mapping(room_payload.get("data"))
        room_id = str(room.get("roomid") or "") or None
        if room_id is None:
            return BiliLiveStatus(
                uid=uid,
                live_status=int(room.get("liveStatus") or room.get("live_status") or 0),
            )
        try:
            detail_payload = await self._get(
                "https://api.live.bilibili.com/room/v1/Room/get_info",
                params={"room_id": room_id},
            )
            detail = _mapping(detail_payload.get("data"))
        except BilibiliAPIError:
            detail = room
        return BiliLiveStatus(
            uid=uid,
            live_status=int(detail.get("live_status") or detail.get("liveStatus") or 0),
            room_id=room_id,
            title=str(detail.get("title") or ""),
            cover_url=str(detail.get("user_cover") or detail.get("cover") or "") or None,
            url=f"https://live.bilibili.com/{room_id}",
            area_name=str(detail.get("area_name") or ""),
        )

    async def creator_name(self, uid: str) -> str:
        payload = await self._get(
            "https://api.bilibili.com/x/space/acc/info", params={"mid": uid}
        )
        return str(_mapping(payload.get("data")).get("name") or uid)

    async def test_cookie(self) -> bool:
        await self.recent_dynamics("2")
        return True

    async def _get(self, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://space.bilibili.com/",
            "Origin": "https://space.bilibili.com",
        }
        cookie = self._config.current.cookie_header
        if cookie:
            headers["Cookie"] = cookie
        try:
            response = await self._client.get(url, params=params, headers=headers)
            response.raise_for_status()
            raw = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BilibiliAPIError(f"Bilibili request failed: {exc}") from exc
        if not isinstance(raw, dict):
            raise BilibiliAPIError("Bilibili response is not a JSON object")
        payload = cast(dict[str, Any], raw)
        if payload.get("code") != 0:
            raise BilibiliAPIError(
                f"Bilibili API error: code={payload.get('code')}, "
                f"message={payload.get('message', '')}"
            )
        return payload


_DYNAMIC_LABELS = {
    "DYNAMIC_TYPE_AV": "发布了新视频",
    "DYNAMIC_TYPE_DRAW": "发布了动态",
    "DYNAMIC_TYPE_WORD": "发布了动态",
    "DYNAMIC_TYPE_ARTICLE": "发布了专栏文章",
    "DYNAMIC_TYPE_FORWARD": "转发了动态",
}


def dynamic_type_label(dynamic_type: str) -> str:
    return _DYNAMIC_LABELS.get(dynamic_type, "发布了新动态")


def parse_dynamic(item: dict[str, Any], uid: str) -> BiliDynamic | None:
    dynamic_id = str(item.get("id_str") or "")
    dynamic_type = str(item.get("type") or "")
    if not dynamic_id or dynamic_type not in _DYNAMIC_LABELS:
        return None
    modules = _mapping(item.get("modules"))
    author = _mapping(modules.get("module_author"))
    timestamp = int(author.get("pub_ts") or 0)
    content = _mapping(modules.get("module_dynamic"))
    major = _mapping(content.get("major"))
    text = ""
    images: list[str] = []
    original_uname: str | None = None
    original_type: str | None = None
    original_text: str | None = None

    if dynamic_type == "DYNAMIC_TYPE_AV":
        archive = _mapping(major.get("archive"))
        text = str(archive.get("title") or "")
        _append_url(images, archive.get("cover"))
    elif dynamic_type == "DYNAMIC_TYPE_DRAW":
        opus = _mapping(major.get("opus"))
        if opus:
            summary = _mapping(opus.get("summary"))
            text = _rich_text(summary)
            title = str(opus.get("title") or "")
            text = f"{title}\n{text}".strip() if title else text
            for picture in _list_of_mappings(opus.get("pics")):
                _append_url(images, picture.get("url"))
        else:
            text = _description_text(content.get("desc"))
            for picture in _list_of_mappings(_mapping(major.get("draw")).get("items")):
                _append_url(images, picture.get("src"))
    elif dynamic_type == "DYNAMIC_TYPE_WORD":
        text = _description_text(content.get("desc"))
    elif dynamic_type == "DYNAMIC_TYPE_ARTICLE":
        text = str(_mapping(major.get("article")).get("title") or "")
    elif dynamic_type == "DYNAMIC_TYPE_FORWARD":
        text = _description_text(content.get("desc"))
        original = item.get("orig")
        if isinstance(original, dict):
            parsed = parse_dynamic(cast(dict[str, Any], original), uid)
            if parsed is not None:
                original_uname = parsed.uname
                original_type = dynamic_type_label(parsed.dynamic_type)
                original_text = parsed.text
                images.extend(parsed.image_urls)

    return BiliDynamic(
        dynamic_id=dynamic_id,
        uid=uid,
        uname=str(author.get("name") or uid),
        dynamic_type=dynamic_type,
        published_at=datetime.fromtimestamp(timestamp, tz=UTC),
        text=text,
        image_urls=tuple(dict.fromkeys(images)),
        url=f"https://t.bilibili.com/{dynamic_id}",
        original_uname=original_uname,
        original_type=original_type,
        original_text=original_text,
    )


def _mapping(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list_of_mappings(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = cast(list[object], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, dict)]


def _rich_text(value: dict[str, Any]) -> str:
    nodes = _list_of_mappings(value.get("rich_text_nodes"))
    if nodes:
        return "".join(str(node.get("orig_text") or node.get("text") or "") for node in nodes)
    return str(value.get("text") or "")


def _description_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return _rich_text(_mapping(value))


def _append_url(target: list[str], value: object) -> None:
    if isinstance(value, str) and value:
        target.append(value)


def _parse_legacy_dynamic(
    card: dict[str, Any], dynamic_id: str, uid: str
) -> BiliDynamic | None:
    item = _mapping(card.get("item"))
    text = str(item.get("description") or card.get("desc") or "")
    title = str(card.get("title") or "")
    if title:
        text = f"{title}\n{text}".strip()
    images: list[str] = []
    for picture in _list_of_mappings(item.get("pictures")):
        _append_url(images, picture.get("img_src"))
    _append_url(images, card.get("pic"))
    profile = _mapping(_mapping(card.get("desc")).get("user_profile"))
    info = _mapping(profile.get("info"))
    return BiliDynamic(
        dynamic_id=dynamic_id,
        uid=uid,
        uname=str(info.get("uname") or uid),
        dynamic_type="DYNAMIC_TYPE_DRAW",
        published_at=datetime.now(UTC),
        text=text,
        image_urls=tuple(dict.fromkeys(images)),
        url=f"https://t.bilibili.com/{dynamic_id}",
    )
