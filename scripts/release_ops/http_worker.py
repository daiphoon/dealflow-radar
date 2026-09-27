"""Internal bounded HTTP worker, launched only by the probe supervisor."""

import json
import sys

from .probes import Contract, _http_once

if __name__ == "__main__":
    print(json.dumps(_http_once(Contract(**json.load(sys.stdin)))))
