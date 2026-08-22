"""取消安全的异步文件 I/O 辅助函数。"""

import asyncio
from typing import Any, Awaitable, Callable, List


async def gather_cancel_on_error(*awaitables: Awaitable[Any]) -> List[Any]:
    """并发等待；任一任务失败或被取消时，取消并回收其它任务。"""
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def run_blocking(function: Callable[..., Any], *args, **kwargs) -> Any:
    """在线程中执行阻塞操作；收到取消时先等待线程收尾再传播取消。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # asyncio.to_thread 的底层线程不能被强制终止。等待它关闭文件句柄后，
        # 外层 finally 才能可靠删除临时文件。
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            try:
                task.exception()
            except Exception:
                pass
        raise cancelled
