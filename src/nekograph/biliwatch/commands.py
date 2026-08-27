"""Administrator-only deterministic commands for BiliWatch."""

from __future__ import annotations

import re

from nekograph.application.commands import CommandDefinition, CommandRegistry
from nekograph.biliwatch.client import dynamic_type_label
from nekograph.biliwatch.config import BiliWatchConfigStore, BiliWatchConfigUpdate
from nekograph.biliwatch.models import Subscription, SubscriptionInput, WatchType
from nekograph.biliwatch.service import BiliWatchService
from nekograph.models import ChatKind, RunContext


def register_biliwatch_commands(
    registry: CommandRegistry,
    service: BiliWatchService,
    config: BiliWatchConfigStore,
) -> None:
    async def watch(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        try:
            parsed = _parse_watch_args(args)
        except ValueError as exc:
            return str(exc)
        existing = await service.store.by_target(
            context.bot_id, parsed.group_id, parsed.uid
        )
        watch_dynamic = parsed.watch_type is WatchType.DYNAMIC
        watch_live = parsed.watch_type is WatchType.LIVE
        if existing is not None:
            watch_dynamic = watch_dynamic or existing.watch_dynamic
            watch_live = watch_live or existing.watch_live
        saved = await service.save_subscription(
            SubscriptionInput(
                bot_id=context.bot_id,
                group_id=parsed.group_id,
                uid=parsed.uid,
                watch_dynamic=watch_dynamic,
                watch_live=watch_live,
                at_all_dynamic=(
                    parsed.at_all
                    if parsed.watch_type is WatchType.DYNAMIC
                    else bool(existing and existing.at_all_dynamic)
                ),
                at_all_live=(
                    parsed.at_all
                    if parsed.watch_type is WatchType.LIVE
                    else bool(existing and existing.at_all_live)
                ),
                filter_forward=bool(existing and existing.filter_forward),
                enabled=True,
            )
        )
        label = "动态" if parsed.watch_type is WatchType.DYNAMIC else "直播"
        suffix = "，并 @全体" if parsed.at_all else ""
        return (
            f"已订阅 {saved.uname}（UID: {saved.uid}）的{label}{suffix}，"
            f"目标群：{saved.group_id}。"
        )

    async def unwatch(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        try:
            group_id, uid = _parse_target(args, "/unwatch -g <群号> <UID>")
        except ValueError as exc:
            return str(exc)
        existing = await service.store.by_target(context.bot_id, group_id, uid)
        if existing is None:
            return f"群 {group_id} 没有订阅 UID {uid}。"
        await service.delete_subscription(existing.subscription_id)
        return f"已移除 {existing.uname}（UID: {uid}）在群 {group_id} 的订阅。"

    async def watchlist(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        group_filter = _optional_group(args)
        subscriptions = [
            item
            for item in await service.store.subscriptions()
            if item.bot_id == context.bot_id
            and (group_filter is None or item.group_id == group_filter)
        ]
        if not subscriptions:
            return "当前没有符合条件的 BiliWatch 订阅。"
        lines = ["BiliWatch 订阅："]
        for item in subscriptions:
            types: list[str] = []
            if item.watch_dynamic:
                types.append("动态" + ("[@全体]" if item.at_all_dynamic else ""))
            if item.watch_live:
                types.append("直播" + ("[@全体]" if item.at_all_live else ""))
            lines.append(
                f"- 群 {item.group_id}：{item.uname}（{item.uid}） "
                f"{'/'.join(types)} 屏蔽转发={'是' if item.filter_forward else '否'}"
            )
        return "\n".join(lines)

    async def watchat(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        try:
            parsed = _parse_watch_args(args, allow_at_flag=False)
        except ValueError as exc:
            return str(exc)
        existing = await service.store.by_target(
            context.bot_id, parsed.group_id, parsed.uid
        )
        if existing is None:
            return f"群 {parsed.group_id} 没有订阅 UID {parsed.uid}。"
        if parsed.watch_type is WatchType.DYNAMIC and not existing.watch_dynamic:
            return "该订阅没有开启动态监测。"
        if parsed.watch_type is WatchType.LIVE and not existing.watch_live:
            return "该订阅没有开启直播监测。"
        updated = existing.model_copy(
            update={
                "at_all_dynamic": (
                    not existing.at_all_dynamic
                    if parsed.watch_type is WatchType.DYNAMIC
                    else existing.at_all_dynamic
                ),
                "at_all_live": (
                    not existing.at_all_live
                    if parsed.watch_type is WatchType.LIVE
                    else existing.at_all_live
                ),
            }
        )
        saved = await service.store.save_subscription(
            _subscription_input(updated),
            uname=updated.uname,
            last_dynamic_timestamp=updated.last_dynamic_timestamp,
        )
        enabled = (
            saved.at_all_dynamic
            if parsed.watch_type is WatchType.DYNAMIC
            else saved.at_all_live
        )
        return f"已{'开启' if enabled else '关闭'}该订阅的 @全体。"

    async def watchfilter(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        try:
            group_id, uid = _parse_target(args, "/watchfilter -g <群号> <UID>")
        except ValueError as exc:
            return str(exc)
        existing = await service.store.by_target(context.bot_id, group_id, uid)
        if existing is None:
            return f"群 {group_id} 没有订阅 UID {uid}。"
        if not existing.watch_dynamic:
            return "该订阅没有开启动态监测。"
        updated = existing.model_copy(
            update={"filter_forward": not existing.filter_forward}
        )
        saved = await service.store.save_subscription(
            _subscription_input(updated),
            uname=updated.uname,
            last_dynamic_timestamp=updated.last_dynamic_timestamp,
        )
        return f"已{'开启' if saved.filter_forward else '关闭'}屏蔽转发动态。"

    async def watchstatus(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        if len(args) != 1 or not args[0].isdigit():
            return "用法：/watchstatus <UID>"
        uid = args[0]
        lines = [f"UID: {uid}"]
        try:
            dynamics = await service.client.recent_dynamics(uid)
            if dynamics:
                latest = max(dynamics, key=lambda item: item.published_at)
                lines.extend(
                    [
                        f"UP主：{latest.uname}",
                        f"最新动态：{latest.dynamic_id}",
                        "发布时间："
                        f"{latest.published_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}",
                    ]
                )
            else:
                lines.append("最新动态：无")
        except Exception as exc:
            lines.append(f"动态查询失败：{exc}")
        try:
            live = await service.client.live_status(uid)
            labels = {0: "未开播", 1: "开播中", 2: "轮播中"}
            lines.append(f"直播状态：{labels.get(live.live_status, f'未知({live.live_status})')}")
            if live.title:
                lines.append(f"直播标题：{live.title}")
            if live.url:
                lines.append(f"直播间：{live.url}")
        except Exception as exc:
            lines.append(f"直播查询失败：{exc}")
        return "\n".join(lines)

    async def watchcookie(context: RunContext, _args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        if not config.current.cookie_header:
            return "尚未配置 B 站 Cookie，请在 Dashboard 的 BiliWatch 页面填写。"
        try:
            await service.client.test_cookie()
            return "B 站 Cookie 可用，动态接口调用成功。"
        except Exception as exc:
            return f"B 站 Cookie 测试失败：{exc}"

    async def watchhelp(context: RunContext, _args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        return (
            "BiliWatch 管理命令（仅管理员私聊）：\n"
            "/watch -g <群号> -w <动态|直播> [-at] <UID>\n"
            "/unwatch -g <群号> <UID>\n"
            "/watchlist [-g <群号>]（兼容 /list）\n"
            "/watchat -g <群号> -w <动态|直播> <UID>（兼容 /at）\n"
            "/watchfilter -g <群号> <UID>（兼容 /filter）\n"
            "/watchstatus <UID>\n"
            "/watchcookie（兼容 /test_cookie）\n"
            "/watchtest <动态链接>\n"
            "/livetest <UID>\n"
            "/watchinterval <秒数>（兼容 /interval）"
        )

    async def watchinterval(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        if len(args) != 1:
            return (
                f"当前轮询间隔：{config.current.poll_interval_seconds} 秒\n"
                "用法：/watchinterval <秒数>（20-3600）"
            )
        try:
            seconds = int(args[0])
        except ValueError:
            return "轮询间隔必须是整数。"
        if not 20 <= seconds <= 3600:
            return "轮询间隔必须在 20 到 3600 秒之间。"
        await config.update(
            BiliWatchConfigUpdate(
                admins=config.current.admins,
                poll_interval_seconds=seconds,
            )
        )
        await service.sync_polling_schedule()
        return f"BiliWatch 轮询间隔已设置为 {seconds} 秒。"

    async def watchtest(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        if len(args) != 1:
            return "用法：/watchtest <B 站动态链接>"
        match = re.search(
            r"(?:bilibili\.com/(?:opus|dynamic)/|t\.bilibili\.com/)(\d+)",
            args[0],
        )
        if match is None:
            return "无法识别动态链接，请使用 t.bilibili.com、dynamic 或 opus 链接。"
        dynamic_id = match.group(1)
        try:
            dynamic = await service.client.dynamic_detail(dynamic_id, "0")
        except Exception as exc:
            return f"动态详情查询失败：{exc}"
        if dynamic is None:
            return f"动态详情查询失败：dynamic_id={dynamic_id}"
        lines = [
            f"{dynamic.uname} {dynamic_type_label(dynamic.dynamic_type)}",
            dynamic.text[:500] + ("..." if len(dynamic.text) > 500 else ""),
            dynamic.url,
        ]
        if dynamic.original_uname:
            lines.append(
                f"原动态：{dynamic.original_uname} "
                f"{dynamic.original_text or ''}".strip()
            )
        if dynamic.image_urls:
            lines.append("图片：" + " ".join(dynamic.image_urls))
        return "\n".join(line for line in lines if line)

    async def livetest(context: RunContext, args: tuple[str, ...]) -> str:
        denied = _authorize(context, config)
        if denied:
            return denied
        if len(args) != 1 or not args[0].isdigit():
            return "用法：/livetest <UID>"
        uid = args[0]
        try:
            live = await service.client.live_status(uid)
        except Exception as exc:
            return f"直播状态查询失败：{exc}"
        try:
            uname = await service.client.creator_name(uid)
        except Exception:
            uname = uid
        labels = {0: "未开播", 1: "开播中", 2: "轮播中"}
        lines = [f"{uname}：{labels.get(live.live_status, f'未知({live.live_status})')} "]
        if live.title:
            lines.append(f"标题：{live.title}")
        if live.url:
            lines.append(f"直播间：{live.url}")
        if live.cover_url:
            lines.append(f"封面：{live.cover_url}")
        return "\n".join(lines).strip()

    definitions = (
        CommandDefinition("/watchhelp", "查看 BiliWatch 命令", watchhelp, "core:biliwatch"),
        CommandDefinition("/watch", "添加或更新 BiliWatch 订阅", watch, "core:biliwatch"),
        CommandDefinition("/unwatch", "移除 BiliWatch 订阅", unwatch, "core:biliwatch"),
        CommandDefinition("/watchlist", "查看 BiliWatch 订阅", watchlist, "core:biliwatch"),
        CommandDefinition("/list", "查看 BiliWatch 订阅（兼容命令）", watchlist, "core:biliwatch"),
        CommandDefinition("/watchat", "切换订阅的 @全体", watchat, "core:biliwatch"),
        CommandDefinition("/at", "切换订阅的 @全体（兼容命令）", watchat, "core:biliwatch"),
        CommandDefinition(
            "/watchfilter", "切换屏蔽转发动态", watchfilter, "core:biliwatch"
        ),
        CommandDefinition("/filter", "切换屏蔽转发动态（兼容命令）", watchfilter, "core:biliwatch"),
        CommandDefinition(
            "/watchstatus", "查询 UP 主动态和直播状态", watchstatus, "core:biliwatch"
        ),
        CommandDefinition(
            "/watchcookie", "测试 B 站 Cookie", watchcookie, "core:biliwatch"
        ),
        CommandDefinition(
            "/test_cookie", "测试 B 站 Cookie（兼容命令）", watchcookie, "core:biliwatch"
        ),
        CommandDefinition(
            "/watchinterval", "修改 BiliWatch 轮询间隔", watchinterval, "core:biliwatch"
        ),
        CommandDefinition(
            "/interval", "修改 BiliWatch 轮询间隔（兼容命令）", watchinterval, "core:biliwatch"
        ),
        CommandDefinition("/watchtest", "预览一条 B 站动态", watchtest, "core:biliwatch"),
        CommandDefinition("/livetest", "查询直播预览", livetest, "core:biliwatch"),
    )
    for definition in definitions:
        registry.register(definition)


class _WatchArguments:
    def __init__(self, group_id: str, watch_type: WatchType, uid: str, at_all: bool) -> None:
        self.group_id = group_id
        self.watch_type = watch_type
        self.uid = uid
        self.at_all = at_all


def _authorize(context: RunContext, config: BiliWatchConfigStore) -> str | None:
    if context.chat.kind is not ChatKind.PRIVATE:
        return "BiliWatch 管理命令只能由管理员私聊使用。"
    if context.actor.user_id not in config.current.admins:
        return "你没有管理 BiliWatch 的权限。"
    return None


def _parse_watch_args(
    args: tuple[str, ...], *, allow_at_flag: bool = True
) -> _WatchArguments:
    group_id = ""
    raw_type = ""
    uid = ""
    at_all = False
    index = 0
    while index < len(args):
        token = args[index]
        if token.casefold() == "-g" and index + 1 < len(args):
            index += 1
            group_id = args[index]
        elif token.casefold() == "-w" and index + 1 < len(args):
            index += 1
            raw_type = args[index].casefold()
        elif token.casefold() == "-at" and allow_at_flag:
            at_all = True
        elif token.isdigit() and not uid:
            uid = token
        index += 1
    aliases = {
        "动态": WatchType.DYNAMIC,
        "dynamic": WatchType.DYNAMIC,
        "直播": WatchType.LIVE,
        "live": WatchType.LIVE,
    }
    if not group_id.isdigit() or raw_type not in aliases or not uid.isdigit():
        command = "/watch" if allow_at_flag else "/watchat"
        suffix = " [-at]" if allow_at_flag else ""
        raise ValueError(f"用法：{command} -g <群号> -w <动态/直播>{suffix} <UID>")
    return _WatchArguments(group_id, aliases[raw_type], uid, at_all)


def _parse_target(args: tuple[str, ...], usage: str) -> tuple[str, str]:
    group_id = ""
    uid = ""
    index = 0
    while index < len(args):
        if args[index].casefold() == "-g" and index + 1 < len(args):
            index += 1
            group_id = args[index]
        elif args[index].isdigit() and not uid:
            uid = args[index]
        index += 1
    if not group_id.isdigit() or not uid.isdigit():
        raise ValueError(f"用法：{usage}")
    return group_id, uid


def _optional_group(args: tuple[str, ...]) -> str | None:
    if not args:
        return None
    group_id, _ = _parse_target((*args, "0"), "/watchlist [-g <群号>]")
    return group_id


def _subscription_input(subscription: Subscription) -> SubscriptionInput:
    return SubscriptionInput(
        bot_id=subscription.bot_id,
        group_id=subscription.group_id,
        uid=subscription.uid,
        watch_dynamic=subscription.watch_dynamic,
        watch_live=subscription.watch_live,
        at_all_dynamic=subscription.at_all_dynamic,
        at_all_live=subscription.at_all_live,
        filter_forward=subscription.filter_forward,
        enabled=subscription.enabled,
    )
