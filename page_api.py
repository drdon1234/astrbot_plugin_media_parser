"""Pages 配置接口，负责读取字段、校验增量修改和安排插件重载。"""

import asyncio
import copy
import hashlib
import json
import math
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from astrbot.api.web import error_response, json_response, request

from .core.logger import logger


_SCHEMA_PATH = Path(__file__).parent / "_conf_schema.json"
_SECRET_PATHS = frozenset({
    "bilibili_enhanced.cookie", "wechat.yuanbao_cookie", "pixiv.cookie",
    "translation.llm.custom_provider.api_key", "proxy.address",
})
_BOUNDS = {
    "message.text_metadata.render_font_size": (16, 42),
    "message.text_metadata.render_line_spacing": (1.0, 3.0),
    "message.text_metadata.render_paragraph_spacing": (0.0, 3.0),
    "message.archive.max_total_size_mb": (1, 4096),
    "download.max_concurrent": (1, None),
    "media_relay.ttl": (30, None),
    "bilibili_enhanced.admin_assist.reply_timeout_minutes": (1, None),
    "bilibili_enhanced.admin_assist.request_cooldown_minutes": (1, None),
}


def _revision(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _get(config: dict, path: str, default: Any = None) -> Any:
    value = config
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return copy.deepcopy(default)
        value = value[key]
    return copy.deepcopy(value)


def _set(config: dict, path: str, value: Any) -> None:
    keys = path.split(".")
    parent = config
    for key in keys[:-1]:
        if key not in parent:
            parent[key] = {}
        if not isinstance(parent[key], dict):
            raise ValueError("配置分组格式错误，请先在基础配置中修正")
        parent = parent[key]
    parent[keys[-1]] = copy.deepcopy(value)


def _fields(schema: dict, prefix: str = "", conditions: Optional[list] = None) -> list:
    result = []
    for key, spec in schema.items():
        path = f"{prefix}{key}"
        inherited = list(conditions or [])
        inherited.extend({"path": f"{prefix}{name}", "value": value}
                         for name, value in spec.get("condition", {}).items())
        if spec["type"] == "object":
            result.extend(_fields(spec["items"], path + ".", inherited))
        elif not spec.get("invisible", False):
            result.append({
                "path": path, "type": spec["type"],
                "title": spec.get("description", key), "hint": spec.get("hint", ""),
                "default": spec.get("default", [] if spec["type"] == "list" else ""),
                "options": spec.get("options", []), "conditions": inherited,
                "secret": path in _SECRET_PATHS,
                "bounds": _BOUNDS.get(path, (0, None)) if spec["type"] in ("int", "float") else None,
            })
    return result


def _group_titles(schema: dict, prefix: str = "") -> dict:
    result = {}
    for key, spec in schema.items():
        if spec["type"] == "object":
            path = prefix + key
            result[path] = spec.get("description", key)
            result.update(_group_titles(spec["items"], path + "."))
    return result


def _validate(value: Any, field: dict) -> None:
    kind = field["type"]
    valid = (
        isinstance(value, str) if kind in ("string", "text") else
        type(value) is bool if kind == "bool" else
        type(value) is int if kind == "int" else
        type(value) in (int, float) and math.isfinite(value) if kind == "float" else
        isinstance(value, list) and all(isinstance(item, str) for item in value) if kind == "list" else False
    )
    if not valid:
        raise ValueError(f"{field['title']}：值的类型不正确")
    if field["options"] and value not in field["options"]:
        raise ValueError(f"{field['title']}：请选择列表中的值")
    if field["bounds"]:
        lower, upper = field["bounds"]
        if value < lower or (upper is not None and value > upper):
            raise ValueError(f"{field['title']}：数值超出允许范围")
    if isinstance(value, (str, list)) and len(value) > 32000:
        raise ValueError(f"{field['title']}：内容过长")


class MediaParserPageAPI:
    """使用 Dashboard 登录身份管理原插件配置。"""

    def __init__(self, plugin: Any, config: dict) -> None:
        self.plugin = plugin
        self.config = config
        self.schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.fields = _fields(self.schema)
        self.field_map = {field["path"]: field for field in self.fields}
        self.instance = uuid.uuid4().hex
        self.active_revision = _revision(config)
        self.reload_error = ""
        self._reload_task: Optional[asyncio.Task] = None
        for route, handler, method in (
            ("settings", self.settings, "GET"),
            ("settings/save", self.save, "POST"),
            ("settings/apply", self.apply, "POST"),
        ):
            plugin.context.register_web_api(
                f"/astrbot_plugin_media_parser/{route}", handler, [method],
                f"流媒体解析配置：{route}",
            )

    async def settings(self) -> Any:
        """返回配置字段、脱敏值、模型选项与当前运行状态。"""
        values, configured = {}, {}
        for field in self.fields:
            path = field["path"]
            value = _get(self.config, path, field["default"])
            values[path] = "" if field["secret"] else value
            if field["secret"]:
                configured[path] = bool(value)
        providers = []
        for provider in self.plugin.context.get_all_providers():
            providers.append(provider.meta().id)
        cfg = self.plugin.config_manager
        return json_response({
            "fields": self.fields, "values": values, "configured": configured,
            "groups": {key: spec["description"] for key, spec in self.schema.items()},
            "group_titles": _group_titles(self.schema),
            "providers": providers, "revision": _revision(self.config),
            "active_revision": self.active_revision, "instance": self.instance,
            "runtime": {
                "cache_available": cfg.download.cache_dir_available,
                "ffmpeg_available": bool(shutil.which("ffmpeg")),
                "active_flows": self.plugin._active_media_flows,
                "reloading": self._reload_task is not None,
                "reload_error": self.reload_error,
            },
        })

    async def save(self) -> Any:
        """校验并保存增量字段，保留未修改的密钥及未知配置。"""
        if self._reload_task is not None:
            return error_response("插件正在重载，请稍后再保存", status_code=409)
        try:
            body = await request.json(default=None)
            if not isinstance(body, dict) or not isinstance(body.get("changes"), dict):
                raise ValueError("请求必须包含修改字段")
            if body.get("revision") != _revision(self.config):
                return error_response("配置已在其他页面或后台更新，请重新载入后再保存", status_code=409)
            candidate = copy.deepcopy(dict(self.config))
            for path, value in body["changes"].items():
                field = self.field_map.get(path)
                if field is None:
                    raise ValueError("包含未知或不可编辑的配置字段")
                _validate(value, field)
                _set(candidate, path, value)
            archive = str(_get(candidate, "message.archive.command", "")).strip()
            clean = str(_get(candidate, "admin.clean_cache_keyword", "清理媒体")).strip()
            if archive and archive == clean:
                raise ValueError("归档命令不能与清理缓存命令相同")
        except (TypeError, ValueError) as exc:
            return error_response(str(exc), status_code=400)
        if not callable(getattr(self.config, "save_config", None)):
            return error_response("当前运行环境不支持持久化配置", status_code=503)
        previous = copy.deepcopy(dict(self.config))
        try:
            self.config.clear()
            self.config.update(candidate)
            self.config.save_config()
        except (OSError, ValueError, TypeError):
            self.config.clear()
            self.config.update(previous)
            logger.exception("Pages 保存配置失败，已恢复内存配置")
            return error_response("保存失败，原配置已保留，请查看日志", status_code=500)
        logger.info("Pages 配置保存完成")
        return json_response({"saved": True, "revision": _revision(self.config),
                              "apply": self._schedule_reload()})

    async def apply(self) -> Any:
        """安排应用已保存配置；运行中的媒体任务完成前不重载。"""
        return json_response({"apply": self._schedule_reload()})

    def _schedule_reload(self) -> str:
        if self._reload_task is not None:
            return "pending"
        if self.plugin._active_media_flows:
            return "busy"
        manager = getattr(self.plugin.context, "_star_manager", None)
        if manager is None:
            return "manual"
        self.reload_error = ""
        self._reload_task = asyncio.create_task(self._reload(manager))
        return "pending"

    async def _reload(self, manager: Any) -> None:
        await asyncio.sleep(1)
        if self.plugin._active_media_flows:
            self.reload_error = "有新的媒体任务开始，请等待完成后再次应用配置"
            self._reload_task = None
            return
        self._reload_task = None
        try:
            result = await manager.reload(self.plugin.name)
            if isinstance(result, tuple) and result[0] is False:
                self.reload_error = "插件重载失败，请查看后台日志并手动重载"
                logger.error(self.reload_error)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, ValueError, OSError) as exc:
            self.reload_error = "插件重载失败，请查看后台日志并手动重载"
            logger.error(f"{self.reload_error}：{exc}")

    async def close(self) -> None:
        """插件卸载时取消尚未执行的重载任务。"""
        if self._reload_task is not None:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None
