import os
import tempfile

os.environ["MESSENGER_DB"] = os.path.join(tempfile.gettempdir(), f"test_messenger_{os.getpid()}.db")

if os.path.exists(os.environ["MESSENGER_DB"]):
    os.remove(os.environ["MESSENGER_DB"])