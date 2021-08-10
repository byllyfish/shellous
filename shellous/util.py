"Implements Redirect enum and various utility functions."

import asyncio
import enum
from typing import Optional, Union


class Redirect(enum.IntEnum):
    "Redirection constants."

    STDOUT = asyncio.subprocess.STDOUT  # -2
    DEVNULL = asyncio.subprocess.DEVNULL  # -3
    CAPTURE = -10
    INHERIT = -11

    def is_custom(self):
        "Return true if this redirect option is not built into asyncio."
        return self in {Redirect.CAPTURE, Redirect.INHERIT}


def decode(data: Optional[bytes], encoding: Optional[str]) -> Union[str, bytes, None]:
    "Utility function to decode optional byte strings."
    if encoding is None or data is None:
        return data
    if " " in encoding:
        encoding, errors = encoding.split(maxsplit=1)
        return data.decode(encoding, errors)
    return data.decode(encoding)
