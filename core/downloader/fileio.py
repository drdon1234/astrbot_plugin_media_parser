"""取消安全的异步文件 I/O 辅助函数。"""

import asyncio
from typing import Any, Awaitable, Callable, List, Optional


async def _wait_for_task_completion(task: asyncio.Task[Any]) -> None:
    """忽略外层重复取消，等待不支持强制终止的线程任务结束。"""
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            continue


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


async def run_blocking(
    function: Callable[..., Any],
    *args,
    cancel_result_cleanup: Optional[Callable[[Any], Any]] = None,
    **kwargs,
) -> Any:
    """在线程中执行阻塞操作；收到取消时先等待线程收尾并清理结果。"""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # asyncio.to_thread 的底层线程不能被强制终止。等待它关闭文件句柄后，
        # 外层 finally 才能可靠删除临时文件。
        await _wait_for_task_completion(task)
        result = None
        if not task.cancelled() and task.exception() is None:
            result = task.result()
        if result is not None and cancel_result_cleanup is not None:
            cleanup_task = asyncio.create_task(
                asyncio.to_thread(cancel_result_cleanup, result)
            )
            await _wait_for_task_completion(cleanup_task)
            if not cleanup_task.cancelled():
                cleanup_task.exception()
        raise cancelled
