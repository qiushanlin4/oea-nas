# -*- coding: utf-8 -*-
"""
filemgr.py —— nasmon 的文件管理后端。

设计要点：
  * 所有路径都被限制在 ROOT（默认 /srv/pool）之内，resolve 后必须仍在 ROOT 下，
    因此 ".." / 软链接逃逸都会被拒绝。
  * 提供：list / mkdir / delete(递归可选) / rename / save_text / upload(二进制)
  * 不做认证 —— 由 nasmon.py 在路由层统一拦截。
"""

import base64
import os
import re
import shutil
import time

ROOT = "/srv/pool"

# 单次写入上限（文本）/ 上传上限（二进制）
MAX_TEXT = 512 * 1024
MAX_UPLOAD = 200 * 1024 * 1024

_SAFE_NAME = re.compile(r"^[^/\\\x00]{1,255}$")


class BadPath(Exception):
    """路径非法或越界。"""


def _root():
    return os.path.realpath(ROOT)


def resolve(rel, must_exist=False):
    """把用户给的相对路径解析成 ROOT 内的绝对路径。

    rel 可以是 "" / "."（表示根），或 "a/b/c.txt"。
    返回的路径保证 realpath 后仍位于 ROOT 之内。
    """
    rel = (rel or "").strip().replace("\\", "/")
    if rel in ("", ".", "/"):
        p = _root()
    else:
        rel = rel.lstrip("/")
        p = os.path.realpath(os.path.join(_root(), rel))
    root = _root()
    if p != root and not p.startswith(root + os.sep):
        raise BadPath("路径越界")
    if must_exist and not os.path.exists(p):
        raise BadPath("不存在")
    return p


def _relpath(p):
    root = _root()
    if p == root:
        return ""
    return os.path.relpath(p, root).replace(os.sep, "/")


def _entry(p, name):
    """构造一条目录项。"""
    try:
        st = os.lstat(p)
    except OSError:
        return None
    is_dir = os.path.isdir(p)
    is_link = os.path.islink(p)
    d = {
        "name": name,
        "path": _relpath(p),
        "type": "dir" if is_dir else ("link" if is_link else "file"),
        "size": 0 if is_dir else st.st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        "mtime_ts": int(st.st_mtime),
        "mode": oct(st.st_mode & 0o777),
    }
    if is_dir:
        try:
            d["count"] = len(os.listdir(p))
        except OSError:
            d["count"] = -1
    return d


def list_dir(rel="", show_hidden=False):
    """列出某个目录的内容。返回 {ok, path, parent, entries:[...]}"""
    try:
        p = resolve(rel)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    if not os.path.isdir(p):
        return {"ok": False, "msg": "不是目录"}
    entries = []
    try:
        names = os.listdir(p)
    except OSError as e:
        return {"ok": False, "msg": "读取失败: %s" % e}
    for name in names:
        if not show_hidden and name.startswith("."):
            continue
        e = _entry(os.path.join(p, name), name)
        if e:
            entries.append(e)
    # 目录在前，然后按名字排序
    entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    cur = _relpath(p)
    parent = "" if not cur else os.path.dirname(cur)
    total = sum(e["size"] for e in entries if e["type"] == "file")
    du = 0
    try:
        st = os.statvfs(p)
        du = {"total": st.f_blocks * st.f_frsize,
              "free": st.f_bavail * st.f_frsize,
              "used": (st.f_blocks - st.f_bfree) * st.f_frsize}
    except OSError:
        pass
    return {"ok": True, "path": cur, "parent": parent,
            "entries": entries, "file_bytes": total, "fs": du}


def _check_name(name):
    name = (name or "").strip()
    if not _SAFE_NAME.match(name) or name in (".", ".."):
        raise BadPath("名字不合法")
    return name


def mkdir(rel, name):
    name = _check_name(name)
    parent = resolve(rel)
    if not os.path.isdir(parent):
        return {"ok": False, "msg": "父目录不存在"}
    target = os.path.join(parent, name)
    if os.path.exists(target):
        return {"ok": False, "msg": "已存在同名项"}
    try:
        os.makedirs(target)
        os.chmod(target, 0o777)
        return {"ok": True, "path": _relpath(target), "msg": "已创建目录"}
    except OSError as e:
        return {"ok": False, "msg": str(e)}


def delete(rel, recursive=False):
    """删除文件或目录。目录非空时必须 recursive=True。"""
    try:
        p = resolve(rel, must_exist=True)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    if p == _root():
        return {"ok": False, "msg": "不能删除根目录"}
    try:
        if os.path.isdir(p) and not os.path.islink(p):
            if recursive:
                shutil.rmtree(p)
                return {"ok": True, "msg": "已删除目录及其内容"}
            try:
                os.rmdir(p)
                return {"ok": True, "msg": "已删除空目录"}
            except OSError:
                return {"ok": False, "msg": "目录非空，需要确认递归删除",
                        "need_confirm": True}
        else:
            os.remove(p)
            return {"ok": True, "msg": "已删除文件"}
    except OSError as e:
        return {"ok": False, "msg": str(e)}


def rename(rel, new_name):
    try:
        p = resolve(rel, must_exist=True)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    new_name = _check_name(new_name)
    if p == _root():
        return {"ok": False, "msg": "不能重命名根目录"}
    target = os.path.join(os.path.dirname(p), new_name)
    if os.path.exists(target):
        return {"ok": False, "msg": "目标已存在"}
    try:
        os.rename(p, target)
        return {"ok": True, "path": _relpath(target), "msg": "已重命名"}
    except OSError as e:
        return {"ok": False, "msg": str(e)}


def read_text(rel, limit=MAX_TEXT):
    try:
        p = resolve(rel, must_exist=True)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    if not os.path.isfile(p):
        return {"ok": False, "msg": "不是文件"}
    size = os.path.getsize(p)
    if size > limit:
        return {"ok": False, "msg": "文件太大（>%d KB），请下载后查看" % (limit // 1024)}
    try:
        with open(p, "rb") as f:
            raw = f.read(limit + 1)
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "msg": "不是文本文件（无法按 UTF-8 解码）"}
    except OSError as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True, "path": _relpath(p), "size": size, "content": text}


def save_text(rel, content):
    """写入/覆盖一个文本文件。rel 含文件名。"""
    try:
        p = resolve(rel)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    parent = os.path.dirname(p)
    if not os.path.isdir(parent):
        return {"ok": False, "msg": "父目录不存在"}
    data = (content or "").encode("utf-8")
    if len(data) > MAX_TEXT:
        return {"ok": False, "msg": "内容超过 %d KB" % (MAX_TEXT // 1024)}
    try:
        with open(p, "wb") as f:
            f.write(data)
        os.chmod(p, 0o666)
        return {"ok": True, "path": _relpath(p), "size": len(data), "msg": "已保存"}
    except OSError as e:
        return {"ok": False, "msg": str(e)}


def upload(rel, filename, b64data):
    """上传二进制：b64data 是 base64 字符串。"""
    filename = _check_name(filename)
    try:
        parent = resolve(rel)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    if not os.path.isdir(parent):
        return {"ok": False, "msg": "父目录不存在"}
    try:
        blob = base64.b64decode(b64data, validate=False)
    except Exception:
        return {"ok": False, "msg": "base64 解码失败"}
    if len(blob) > MAX_UPLOAD:
        return {"ok": False, "msg": "超过 %d MB 上限" % (MAX_UPLOAD // 1024 // 1024)}
    target = os.path.join(parent, filename)
    try:
        with open(target, "wb") as f:
            f.write(blob)
        os.chmod(target, 0o666)
        return {"ok": True, "path": _relpath(target), "size": len(blob),
                "msg": "上传成功"}
    except OSError as e:
        return {"ok": False, "msg": str(e)}


def download(rel):
    """返回 (abs_path, size) 供 HTTP 层流式发送。"""
    p = resolve(rel, must_exist=True)
    if not os.path.isfile(p):
        raise BadPath("不是文件")
    return p, os.path.getsize(p)


def usage(rel=""):
    """递归统计目录大小（可能较慢，限制深度/数量）。"""
    try:
        p = resolve(rel)
    except BadPath as e:
        return {"ok": False, "msg": str(e)}
    total = 0
    files = 0
    dirs = 0
    for root, dnames, fnames in os.walk(p):
        dirs += len(dnames)
        for fn in fnames:
            fp = os.path.join(root, fn)
            try:
                total += os.lstat(fp).st_size
                files += 1
            except OSError:
                pass
    return {"ok": True, "bytes": total, "files": files, "dirs": dirs}
