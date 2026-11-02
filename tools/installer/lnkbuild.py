# -*- coding: utf-8 -*-
"""
Shell Link (.lnk) 二进制生成器 —— 不依赖 COM / pywin32。

依据 MS-SHLLINK 规范构造最小可用链接：
  ShellLinkHeader + LinkInfo(VolumeID + LocalBasePath) + StringData(UTF-16)
不含 LinkTargetIDList，由 Windows 通过 LinkInfo + RELATIVE_PATH 解析目标。

对外接口：
    make_lnk(path, target, arguments="", working_dir="", icon="", description="",
             show_cmd=1, run_as_admin=False)
"""
import struct
import os
import uuid

CLSID_SHELLLINK = bytes([
    0x01, 0x14, 0x02, 0x00,
    0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x46,
])

# LinkFlags
HAS_LINK_TARGET_IDLIST = 0x00000001
HAS_LINK_INFO      = 0x00000002
HAS_NAME           = 0x00000004
HAS_RELATIVE_PATH  = 0x00000008
HAS_WORKING_DIR    = 0x00000010
HAS_ARGUMENTS      = 0x00000020
HAS_ICON_LOCATION  = 0x00000040
IS_UNICODE         = 0x00000080
RUN_AS_USER        = 0x00002000

# 路径解析 IDList 的首选方案：调 shell32.SHParseDisplayName 让 Windows 自己生成
# （回读 SHGetPathFromIDListW 校验）。旧"克隆范本并替换长文件名"的方案已弃用——
# 0x31/0x32 项内含短文件名(如 PROGRA~1)与资源串字段，只换长名会残留短名，
# Explorer 解析时用短名拼出错误路径（实测报"指定路径不存在"指向 Everything.exe）。
_TEMPLATE_LNK = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Everything.lnk"


def _extract_template():
    d = open(_TEMPLATE_LNK, "rb").read()
    flags = struct.unpack_from("<I", d, 20)[0]
    if not (flags & HAS_LINK_TARGET_IDLIST):
        raise RuntimeError("模板 lnk 不含 IDList，无法复用")
    idlist_size = struct.unpack_from("<H", d, 76)[0]
    p = 78
    end = 78 + idlist_size
    items = []
    while p < end - 1:
        cb = struct.unpack_from("<H", d, p)[0]
        if cb == 0:
            break
        items.append(d[p:p + cb])
        p += cb
    # items: [0]=我的电脑, [1]=驱动器(C:\), [2]=文件夹项模板, [3]=文件夹项, [4]=文件项模板
    return items[0], items[1], items[2], items[4]


def _replace_long_name(item_bytes, old_name, new_name):
    old = old_name.encode("utf-16-le") + b"\x00\x00"
    idx = item_bytes.find(old)
    if idx < 0:
        raise RuntimeError("模板长文件名未找到: " + old_name)
    new = new_name.encode("utf-16-le") + b"\x00\x00"
    new_item = item_bytes[:idx] + new + item_bytes[idx + len(old):]
    return struct.pack("<H", len(new_item)) + new_item[2:]


def build_idlist(target):
    """为绝对路径 target 构造合法的 LinkTargetIDList。
    首选 shell32.SHParseDisplayName（系统生成+回读校验，保证正确）。"""
    import ctypes
    target = os.path.abspath(target)
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, 0)
    try:
        pidl = ctypes.c_void_p()
        hr = shell32.SHParseDisplayName(target, None, ctypes.byref(pidl), 0, None)
        if hr != 0 or not pidl.value:
            raise OSError("SHParseDisplayName 失败: 0x%08x" % (hr & 0xFFFFFFFF))
        size = shell32.ILGetSize(pidl)
        data = ctypes.string_at(pidl, size)  # 已含终结符
        buf = ctypes.create_unicode_buffer(2048)
        if shell32.SHGetPathFromIDListW(pidl, buf):
            if buf.value.lower() != target.lower():
                raise RuntimeError("IDList 回读路径不一致: " + buf.value)
        shell32.ILFree(pidl)
    finally:
        ole32.CoUninitialize()
    return struct.pack("<H", len(data)) + data


FILE_ATTRIBUTE_NORMAL = 0x00000080

# ShowCommand
SW_SHOWNORMAL        = 1
SW_SHOWMAXIMIZED     = 3
SW_SHOWMINNOACTIVE   = 7


def _ansi(text):
    """LinkInfo 里的路径按规范是 ANSI（本机为 cp936）。"""
    return text.encode("cp936", errors="replace") + b"\x00"


def _strdata(text):
    """StringData 条目：2 字节字符数（含结尾 NUL）+ UTF-16LE 内容 + NUL。"""
    payload = text.encode("utf-16-le") + b"\x00\x00"
    return struct.pack("<H", len(text) + 1) + payload


def _build_link_info(local_base_path, drive_type=3):
    volume_id = struct.pack("<IIII", 17, drive_type, 0, 0x10) + b"\x00"
    local_base = _ansi(local_base_path)
    suffix = b"\x00"

    header_size = 0x1C
    vol_off = header_size
    local_off = vol_off + len(volume_id)
    suffix_off = local_off + len(local_base)
    link_info_size = suffix_off + len(suffix)

    head = struct.pack(
        "<IIIIIII",
        link_info_size, header_size, 1,   # flags=1 → VolumeIDAndLocalBasePath
        vol_off, local_off, 0, suffix_off,
    )
    return head + volume_id + local_base + suffix


def make_lnk(path, target, arguments="", working_dir="", icon="",
             description="", show_cmd=SW_SHOWNORMAL, run_as_admin=False):
    target = os.path.abspath(target)

    flags = HAS_LINK_TARGET_IDLIST | HAS_LINK_INFO | HAS_RELATIVE_PATH | IS_UNICODE
    if description:
        flags |= HAS_NAME
    if working_dir:
        flags |= HAS_WORKING_DIR
    if arguments:
        flags |= HAS_ARGUMENTS
    if icon:
        flags |= HAS_ICON_LOCATION
    if run_as_admin:
        flags |= RUN_AS_USER

    header = (
        struct.pack("<I", 0x4C)
        + CLSID_SHELLLINK
        + struct.pack("<I", flags)
        + struct.pack("<I", FILE_ATTRIBUTE_NORMAL)
        + struct.pack("<Q", 0)          # CreationTime
        + struct.pack("<Q", 0)          # AccessTime
        + struct.pack("<Q", 0)          # WriteTime
        + struct.pack("<I", 0)          # FileSize
        + struct.pack("<i", 0)          # IconIndex
        + struct.pack("<I", show_cmd)
        + struct.pack("<H", 0)          # HotKey
        + struct.pack("<H", 0)          # Reserved1
        + struct.pack("<I", 0)          # Reserved2
        + struct.pack("<I", 0)          # Reserved3
    )
    assert len(header) == 76, "头部必须是 76 字节，实际 %d" % len(header)

    drive = os.path.splitdrive(target)[0]
    drive_type = 3 if drive else 5

    # 规范写法：LocalBasePath 只放目录，RELATIVE_PATH 只放相对目录的文件名。
    # 之前把完整绝对路径同时塞进两者，Windows 会拼成一个不存在的路径，
    # 导致双击无任何反应。
    target_dir = os.path.dirname(target)
    target_name = os.path.basename(target)

    # 关键：加上规范的 LinkTargetIDList，否则现代 Windows(ShellExecute)无法解析
    # 目标，双击会静默失败（错误 1155）。用系统范本克隆出指向本路径的 IDList。
    idlist = build_idlist(target)

    blob = header + idlist + _build_link_info(target_dir, drive_type)

    if description:
        blob += _strdata(description)
    blob += _strdata(target_name)                  # RELATIVE_PATH = 仅文件名
    if working_dir:
        blob += _strdata(working_dir)
    if arguments:
        blob += _strdata(arguments)
    if icon:
        blob += _strdata(icon)

    import os as _os
    try:
        with open(path, "wb") as f:
            f.write(blob)
    except PermissionError:
        # 目标被 Explorer/360 持锁时原地覆盖写
        with open(path, "r+b") as f:
            f.seek(0)
            f.write(blob)
            f.truncate()
    return path, len(blob)


def read_lnk_info(path):
    """把生成的 .lnk 解析回来，用于自检结构是否自洽。"""
    with open(path, "rb") as f:
        data = f.read()

    info = {"size": len(data)}
    if len(data) < 76:
        info["error"] = "文件过短"
        return info

    header_size, = struct.unpack_from("<I", data, 0)
    info["header_size"] = header_size
    info["clsid_ok"] = data[4:20] == CLSID_SHELLLINK
    flags, = struct.unpack_from("<I", data, 20)
    info["flags"] = hex(flags)
    info["show_cmd"], = struct.unpack_from("<I", data, 60)

    off = 76
    if flags & HAS_LINK_TARGET_IDLIST:
        ils, = struct.unpack_from("<H", data, off)
        off += 2 + ils
    if flags & HAS_LINK_INFO:
        li_size, li_hdr, li_flags, vol_off, local_off, cnrl_off, suffix_off = \
            struct.unpack_from("<IIIIIII", data, off)
        info["link_info_size"] = li_size
        base = off
        raw = data[base + local_off: base + suffix_off]
        info["local_base_path"] = raw.split(b"\x00")[0].decode("cp936", errors="replace")
        off += li_size

    strings = {}
    order = []
    if flags & HAS_NAME:
        order.append("name")
    if flags & HAS_RELATIVE_PATH:
        order.append("relative_path")
    if flags & HAS_WORKING_DIR:
        order.append("working_dir")
    if flags & HAS_ARGUMENTS:
        order.append("arguments")
    if flags & HAS_ICON_LOCATION:
        order.append("icon_location")

    for key in order:
        if off + 2 > len(data):
            break
        n, = struct.unpack_from("<H", data, off)
        off += 2
        raw = data[off: off + n * 2]
        off += n * 2
        strings[key] = raw[:-2].decode("utf-16-le", errors="replace")

    info["strings"] = strings
    info["leftover_bytes"] = len(data) - off
    return info


if __name__ == "__main__":
    import json
    p, n = make_lnk(
        r"F:\AI小说工具\__lnk_probe.lnk",
        r"C:\Windows\System32\cmd.exe",
        arguments="/c echo ok > \"F:\\AI小说工具\\__marker.txt\"",
        working_dir=r"F:\AI小说工具",
        description="测试快捷方式",
    )
    print(json.dumps(read_lnk_info(p), ensure_ascii=False, indent=2))
    print("字节数:", n)
