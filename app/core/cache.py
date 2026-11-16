"""轻量 TTL 缓存（线程安全，进程内）。

用途：资料列表 / 工作台等"读多写少"的查询结果缓存。
- 只缓存纯数据（dict / list），严禁缓存涉及 Session / ORM 对象的引用。
- 默认 5 秒过期；容量超限时淘汰最久未使用项。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


class TTLCache:
    """简单线程安全 TTL 缓存（进程内，单机部署足够）。"""

    def __init__(self, default_ttl: float = 5.0, max_items: int = 256) -> None:
        self._default_ttl = default_ttl
        self._max_items = max_items
        self._data: dict[str, tuple[float, float, Any]] = {}  # key -> (expire_ts, last_used, value)
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            expire_ts, _last, value = item
            if now > expire_ts:
                self._data.pop(key, None)
                return default
            # 更新最近使用
            self._data[key] = (expire_ts, now, value)
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        expire = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            self._data[key] = (expire, time.monotonic(), value)
            if len(self._data) > self._max_items:
                # 淘汰最久未使用
                oldest = min(self._data.items(), key=lambda kv: kv[1][1])
                self._data.pop(oldest[0], None)

    def invalidate(self, prefix: str = "") -> int:
        """按 key 前缀失效；prefix 为空则清空全部。返回失效条数。"""
        with self._lock:
            if not prefix:
                n = len(self._data)
                self._data.clear()
                return n
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# 全局单例（模块级导入即用）
cache = TTLCache()

# 未命中哨兵：区分"缓存里没有"与"缓存了 None"——
# 曾用 `hit is not None` 判命中，导致返回 None 的查询永不缓存、每次重算（2026-10-16 修）
_MISS = object()


def cache_key(prefix: str, *parts: Any) -> str:
    """构造缓存键：prefix 为纯净前缀（供 invalidate 按前缀失效），
    parts 按类型编码避免 str/int 冲突（如 '1' 与 1）。"""
    encoded = "|".join(f"{type(p).__name__}:{p!r}" for p in parts)
    return f"{prefix}|{encoded}" if encoded else prefix


def cached(prefix: str, ttl: Optional[float] = None) -> Callable:
    """装饰器：对纯函数结果做 TTL 缓存。

    注意：仅适用于参数可哈希、返回纯 JSON 数据的函数；
    若函数返回与 Session 绑定的对象，不要使用本装饰器。
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = cache_key(prefix, args, tuple(sorted(kwargs.items())))
            hit = cache.get(key, _MISS)
            if hit is not _MISS:
                return hit
            result = fn(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator

def invalidate_lists() -> None:
    """资料维度缓存统一失效（doc_list + dashboard）。

    资料/分类/标签/用户的写操作全部走本入口（含批量整理），
    统一口径后不再可能出现"改了什么但某处计数不刷新"的散弹式遗漏。
    注意：通知侧写操作仍用 invalidate_dashboard（不影响 doc_list）。
    """
    cache.invalidate("doc_list")
    cache.invalidate("dashboard")


def invalidate_dashboard() -> None:
    """工作台缓存统一失效（通知写操作后调用）。

    2026-08-30 自 routers/dashboard.py 下沉到缓存层，消除 service→router
    的分层倒置；语义不变（cache.invalidate("dashboard")）。
    """
    cache.invalidate("dashboard")
