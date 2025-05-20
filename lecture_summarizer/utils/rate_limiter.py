import time
from rich.progress import Progress



def sleep_with_progress(progress: Progress, task, sleep_time: float, default_description: str) -> None:
    target_time = time.time() + sleep_time
    while True:
        remaining = target_time - time.time()
        if remaining <= 0:
            break
        progress.update(task, description=f"Sleeping for {remaining:.1f} sec due to rate limit")
        time.sleep(min(0.5, remaining))
    progress.update(task, description=default_description)
