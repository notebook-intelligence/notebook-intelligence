# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import os
import base64
from typing import Set
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet
import asyncio
from tornado import ioloop

_jupyter_root_dir: str = None
_enabled_tools: Set[str] = None

def set_jupyter_root_dir(root_dir: str):
    global _jupyter_root_dir
    _jupyter_root_dir = root_dir

def get_jupyter_root_dir() -> str:
    return _jupyter_root_dir

def extract_llm_generated_code(code: str) -> str:
    if code.endswith("```"):
        code = code[:-3]

    lines = code.split("\n")
    if len(lines) < 2:
        return code

    num_lines = len(lines)
    start_line = -1
    end_line = num_lines

    for i in range(num_lines):
        if start_line == -1:
            if lines[i].lstrip().startswith("```"):
                start_line = i
                continue
        else:
            if lines[i].lstrip().startswith("```"):
                end_line = i
                break

    if start_line != -1:
        lines = lines[start_line+1:end_line]

    return "\n".join(lines)

def encrypt_with_password(password: str, data: bytes) -> bytes:
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=1200000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    f = Fernet(key)
    encrypted_data = f.encrypt(data)

    return salt + encrypted_data

def decrypt_with_password(password: str, encrypted_data_with_salt: bytes) -> bytes:
    salt = encrypted_data_with_salt[:16]
    encrypted_data = encrypted_data_with_salt[16:]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=1200000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    f = Fernet(key)
    decrypted_data = f.decrypt(encrypted_data)

    return decrypted_data

def get_enabled_builtin_tools_in_env() -> Set[str]:
  global _enabled_tools
  if _enabled_tools is not None:
    return _enabled_tools
  
  # load enabled built-in tools from environment variable
  _enabled_tools = set()
  enabled_tools_list = os.getenv('NBI_ENABLED_BUILTIN_TOOLS', '').split(',')
  for tool in enabled_tools_list:
    tool = tool.strip() 
    if tool:
      _enabled_tools.add(tool)
  return _enabled_tools

def is_builtin_tool_enabled_in_env(tool: str) -> bool:
  return tool in get_enabled_builtin_tools_in_env()

class ThreadSafeWebSocketConnector():
  def __init__(self, websocket_handler):
    self.io_loop = ioloop.IOLoop.current()
    self.websocket_handler = websocket_handler

  def write_message(self, message: dict):
    def _write_message():
        self.websocket_handler.write_message(message)
    asyncio.set_event_loop(self.io_loop.asyncio_loop)
    self.io_loop.asyncio_loop.call_soon_threadsafe(_write_message)
