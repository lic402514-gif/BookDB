"""本机密钥加密（Windows DPAPI）。

为什么需要它：GUI 允许直接填 DeepSeek API Key，但**明文落盘**不可接受 ——
`settings.json` 可能被同步盘、备份工具、截图或误分享带出去。

做法：用 Windows 自带的 **DPAPI**（`CryptProtectData` / `CryptUnprotectData`）
加密后再写进 `settings.json`：

- 密文只能在**同一 Windows 用户**（默认 machine/user 作用域）下解密；
- 换账户、换机器、拷贝走 `settings.json` 都解不开；
- 不引入第三方依赖（`ctypes` 调 crypt32）；
- 存储格式带前缀 `dpapi-v1:`，将来换算法/平台可平滑迁移。

局限（照实说）：它防的是「文件被拷走 / 被别的账户读到」，防不住
**以当前用户身份运行的恶意程序** —— 这一点任何本地密钥存储都一样。

非 Windows 平台：`available()` 为 False，调用方应提示改用环境变量
`DEEPSEEK_API_KEY`（本项目主目标是 Windows）。
"""

from __future__ import annotations

import base64
import ctypes
import sys

#: 密文前缀（版本标记 + 算法标识）
PREFIX = "dpapi-v1:"

#: 解密失败 / 平台不支持
class SecretError(RuntimeError):
    """密钥无法加密或解密。"""


def available() -> bool:
    """当前平台是否支持本机加密（仅 Windows）。"""
    return sys.platform == "win32"


def protect(text: str) -> str:
    """明文 → 可落盘的密文字符串（带 `dpapi-v1:` 前缀）。"""
    if not text:
        raise ValueError("要加密的内容为空")
    if not available():
        raise SecretError(
            "当前系统不支持本机加密存储（仅 Windows 可用）。"
            "请改用环境变量 DEEPSEEK_API_KEY。"
        )
    return PREFIX + base64.b64encode(_protect_bytes(text.encode("utf-8"))).decode("ascii")


def unprotect(token: str) -> str:
    """密文字符串 → 明文；格式不符或非本用户加密时抛 `SecretError`。"""
    if not token or not token.startswith(PREFIX):
        raise SecretError("不是本程序保存的密钥格式")
    try:
        raw = base64.b64decode(token[len(PREFIX):], validate=True)
    except (ValueError, TypeError) as exc:
        raise SecretError("密钥密文损坏") from exc
    try:
        return _unprotect_bytes(raw).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretError("密钥密文损坏") from exc


# ─────────────────────────────────────────────────────────── Windows 实现
if sys.platform == "win32":  # pragma: no cover - 平台分支由 available() 控制

    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob_from(data: bytes) -> tuple["_Blob", ctypes.Array]:
        """构造入参 BLOB；**返回 buffer 引用**，防止被 GC 提前回收。"""
        buf = ctypes.create_string_buffer(data, len(data))
        blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        return blob, buf

    def _take(blob: "_Blob") -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob.pbData, ctypes.c_void_p))

    def _raise_winerror(action: str) -> None:
        code = ctypes.get_last_error()
        raise SecretError(f"{action}失败（WinError {code}）")

    def _protect_bytes(data: bytes) -> bytes:
        blob_in, _keep = _blob_from(data)
        blob_out = _Blob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob_in), "BookDB 密钥", None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
        if not ok:
            _raise_winerror("加密密钥")
        return _take(blob_out)

    def _unprotect_bytes(raw: bytes) -> bytes:
        blob_in, _keep = _blob_from(raw)
        blob_out = _Blob()
        descr = wintypes.LPWSTR()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), ctypes.byref(descr), None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out),
        )
        if not ok:
            _raise_winerror("解密密钥（可能不是本用户保存的）")
        if descr:
            _kernel32.LocalFree(ctypes.cast(descr, ctypes.c_void_p))
        return _take(blob_out)

else:  # pragma: no cover - 非 Windows 不走加密路径

    def _protect_bytes(data: bytes) -> bytes:
        raise SecretError("当前系统不支持 DPAPI")

    def _unprotect_bytes(raw: bytes) -> bytes:
        raise SecretError("当前系统不支持 DPAPI")
