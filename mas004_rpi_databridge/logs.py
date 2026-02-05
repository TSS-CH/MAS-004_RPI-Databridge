import os
import time
import json
from collections import deque

DEFAULT_LOGDIR = "/var/log/mas004_rpi_databridge"

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

class LogBus:
    def __init__(self, logdir: str = DEFAULT_LOGDIR, mem_keep: int = 500):
        self.logdir = logdir
        ensure_dir(self.logdir)
        self.mem = { }  # component -> deque
        self.mem_keep = mem_keep

    def write(self, component: str, direction: str, text: str, extra=None):
        ts = time.time()
        rec = {"ts": ts, "dir": direction, "text": text, "extra": extra or {}}
        if component not in self.mem:
            self.mem[component] = deque(maxlen=self.mem_keep)
        self.mem[component].append(rec)

        fn = os.path.join(self.logdir, f"{component}.log")
        with open(fn, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def tail_mem(self, component: str, n: int = 200):
        q = self.mem.get(component)
        if not q:
            return []
        return list(q)[-n:]
