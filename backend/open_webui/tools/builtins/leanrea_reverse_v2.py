'''"""Open Web Vibes builtin: leanrea Reverse Engineer

Generated from the WebUI tool export `custom_leanrea_reverse_v2` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: leanrea Reverse Engineer
Exported functions: answer, copy, decompile, ensure, extract, find, handoff, install, log, outgrep, parse, plan, recon, resume, route, rules, run, search, slice, stop, toolchain

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
"\nleanrea - Reverse Engineer Anything, as a native Open WebUI tool (portable build).\n\nSingle file. Nothing outside this file is required, so the tool export imports on any\nmachine. The parser source and the Ghidra post-script are embedded and written to the\nproject root on first use.\n\nBuilt to match leanrea's own rules and router (github.com/aicodeking/leanrea):\n  - work on a copy, each extraction in its own dir under the root\n  - route by file -b and magic bytes first, then the cheapest inventory\n  - search extracted trees with rg -uu (bundlers hide code in dot folders)\n  - never cat a big or minified file, slice around the needle\n  - batch independent probes into ONE shell call with markers, cap output with head\n  - answer from evidence: seen (cite file:offset or a symbol) / inferred / unknown.\n    Unknown is not absent. Static reading is not observed execution.\n  - a missing tool is not a blocker: use the fallback in the same row, or install it\n  - turns cost more than bytes: aim for 2-4 calls\n\nWhat leanrea does not have and this adds:\n  - durable per-project state (log.jsonl, log.md, state.json) so a crash or context loss\n    costs nothing. resume() and handoff() rebuild working context from disk.\n  - context-budget accounting against a configurable ceiling (256k here, 128k elsewhere)\n  - install() and ensure() so a target that needs a tool the machine lacks can get it\n\nPortability rules this build follows:\n  - no absolute paths. The root comes from LEANREA_ROOT, else $TMPDIR/re, else /tmp/re\n  - every tool is resolved with which, and rows carry a fallback for missing tools\n  - the tool interpreter cannot import capstone/pefile/pyelftools, so those run under the\n    system Python. The parser reports that instead of pretending.\n  - package manager is detected per machine (brew, dnf, apt-get, pacman, zypper)\n"
import ast
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from typing import Optional
from pydantic import BaseModel
PARSER_SRC = '\n#!/usr/bin/env python3\n"""leanrea parsers - formats with no command-line tool available. Stdlib only."""\nimport json\nimport os\nimport plistlib\nimport struct\nimport sys\nimport zipfile\nimport zlib\n\nSIGS = {\n    "gzip": b"\\x1f\\x8b", "bzip2": b"BZh", "xz": b"\\xfd7zXZ\\x00", "zstd": b"\\x28\\xb5\\x2f\\xfd",\n    "7z": b"7z\\xbc\\xaf\\x27\\x1c", "zip": b"PK\\x03\\x04", "cpio_newc": b"070701",\n    "squashfs_le": b"hsqs", "squashfs_be": b"sqsh", "jffs2_le": b"\\x19\\x85\\x20\\xcd",\n    "yaffs_le": b"YAFF", "uboot_uimage": b"\\x27\\x05\\x19\\x56", "dtb": b"\\xd0\\x0d\\xfe\\xed",\n    "elf": b"\\x7fELF", "pe": b"MZ", "macho_fat": b"\\xca\\xfa\\xed\\xfe",\n    "macho_thin": b"\\xfe\\xed\\xfa\\xcf", "tar_ustar": b"ustar", "lzo": b"\\x89LZO\\xda\\x00\\x0a",\n    "lz4_frame": b"\\x04\\x06\\x10\\x62", "hermes": b"\\xc6\\x1f\\xbc\\x03", "wasm": b"\\x00\\x61\\x73\\x6d",\n    "dex": b"dex\\n03", "sqlite": b"SQLite format 3\\x00", "plist_bin": b"bplist00",\n    "dart_snapshot": b"\\xf5\\xf5\\xdc\\xdc",\n}\n\nELF_MACHINES = {0x03: "x86", 0x08: "mips", 0x14: "ppc", 0x20: "sparc", 0x32: "ia64",\n               0x3e: "x86_64", 0x28: "arm", 0x2a: "aarch64", 0x3f: "riscv",\n               0x137: "loongarch", 0x220: "riscv"}\nPE_MACHINES = {0x014c: "x86", 0x8664: "x86_64", 0xAA64: "ARM64", 0x01c0: "ARM", 0x0200: "IA64"}\nPE_DATADIRS = {0: "export", 1: "import", 2: "resource", 3: "exception", 4: "security",\n               5: "base_reloc", 6: "debug", 9: "tls", 11: "load_config", 13: "clr_runtime"}\nMACHO_CMDS = {0x1: "SEGMENT", 0x3: "SYMTAB", 0x4: "DYSYMTAB", 0x6: "LOAD_DYLIB",\n              0x8: "ID_DYLIB", 0xc: "LOAD_WEAK_DYLIB", 0x19: "SEGMENT_64", 0x1b: "SYMTAB_64",\n              0x1c: "RPATH", 0x21: "CODE_SIGNATURE", 0x28: "FUNCTION_STARTS", 0x2a: "MAIN",\n              0x2e: "BUILD_VERSION", 0x30: "DYLD_CHAINED_FIXUPS", 0x32: "CHAINED_FIXUPS"}\nMACHO_PLATFORMS = {1: "macos", 2: "ios", 3: "tvos", 4: "watchos", 6: "macos_cat", 8: "visionos"}\nWASM_SECTIONS = {0: "custom", 1: "type", 2: "import", 3: "function", 4: "table", 5: "memory",\n                 6: "global", 7: "export", 8: "start", 9: "element", 10: "code", 11: "data",\n                 12: "datacount"}\nAPK_SIG_IDS = {0x200: "V1_ARC", 0x210: "SHA256_RSA2048", 0x220: "SHA256_RSA4096",\n               0x230: "SHA512_RSA4096", 0x240: "SHA256_ECDSA256", 0x250: "SHA256_RSA8192",\n               0x300: "CAP_VERSION", 0x400: "FLEXIBLE_VERSION", 0x401: "V3_SIGNATURE",\n               0x512: "V1", 0x7100: "V3_SIGNERS"}\nCS_BLOB_TYPES = {0: "SuperBlob", 1: "Entitlements", 2: "Requirements", 4: "CodeResources"}\nTYPE_STRING = 3\n\n\ndef scan(data, path):\n    hits = {}\n    for name, sig in SIGS.items():\n        pos, count, first = 0, 0, None\n        while True:\n            i = data.find(sig, pos)\n            if i < 0:\n                break\n            count += 1\n            if first is None:\n                first = i\n            pos = i + len(sig)\n            if count > 2000:\n                break\n        if count:\n            hits[name] = {"count": count, "first_offset": first}\n    out = {"file": path, "size": len(data), "signatures": hits}\n    try:\n        sample = data[:1_000_000]\n        ratio = len(zlib.compress(sample, 6)) / max(1, len(sample))\n        out["compressibility"] = round(ratio, 4)\n        out["reading"] = "likely compressed/encrypted regions" if ratio < 0.62 else "mostly plain"\n    except Exception:\n        pass\n    if hits.get("dart_snapshot"):\n        off = hits["dart_snapshot"]["first_offset"]\n        out["flutter_snapshot_hash"] = data[off + 20:off + 52].hex()\n    return out\n\n\ndef dex(data, path):\n    """DEX. Read sections from the file\'s own map_list, not hardcoded header indices.\n    The header indices shift; the map is the layout the file actually declares."""\n    if data[:4] != b"dex\\n":\n        return {"error": "not a DEX file"}\n    ver = data[4:7].decode("ascii", "replace")\n    map_off = struct.unpack_from("<I", data, 0x34)[0]\n    nmap = struct.unpack_from("<I", data, map_off)[0]\n    MAP = {0: "header", 1: "string_ids", 2: "type_ids", 3: "proto_ids", 4: "field_ids",\n           5: "method_ids", 6: "class_defs", 7: "call_site_ids", 8: "call_site_defs",\n           9: "debug_info", 10: "code", 11: "string_data"}\n    # Header indices are authoritative for counts (verified against a real DEX). The map is\n    # read as confirmation only, because map_entry size is 16 bytes and a 12-byte read drifts\n    # after the contiguous types 0-12, producing garbage like type 8193.\n    HEADER = {0x38: ("string_ids", 4), 0x40: ("type_ids", 4), 0x48: ("proto_ids", 16),\n             0x50: ("field_ids", 32), 0x58: ("method_ids", 40), 0x60: ("class_defs", 32)}\n    sections = {}\n    for off, (name, entsize) in HEADER.items():\n        if off + 8 <= len(data):\n            size, base = struct.unpack_from("<II", data, off)\n            sections[name] = {"count": size // entsize, "offset": base, "bytes": size}\n    p, confirmed = map_off + 4, 0\n    for _ in range(nmap):\n        if p + 16 > len(data):\n            break\n        typ, count, offset, unused = struct.unpack_from("<IIII", data, p)\n        name = MAP.get(typ)\n        if name and name in sections:\n            if sections[name]["count"] == count and sections[name]["offset"] == offset:\n                confirmed += 1\n        p += 16\n    # string_ids is an index of uint32 offsets relative to the string_data base; string_data\n    # count is the byte extent. Decode through the index so order matches the file.\n    strings = []\n    ids, pool = sections.get("string_ids"), sections.get("string_data")\n    if ids and pool:\n        for i in range(min(ids["count"], 5000)):\n            pos = ids["offset"] + i * 4\n            if pos + 4 > len(data):\n                break\n            off = pool["offset"] + struct.unpack_from("<I", data, pos)[0]\n            end = data.find(b"\\0", off)\n            if end < 0:\n                break\n            s = data[off:end]\n            if s:\n                strings.append(s.decode("utf-8", "replace"))\n    elif pool:\n        raw = data[pool["offset"]:pool["offset"] + pool["count"]]\n        strings = [x.decode("utf-8", "replace") for x in raw.split(b"\\0") if x]\n    lens = [len(s) for s in strings]\n    short = sum(1 for s in lens if s <= 2)\n    roots = sorted({s.split(".")[0] for s in strings if "." in s and not s.startswith("L")})\n    return {\n        "file": path,\n        "dex_version": ver,\n        "map_list_offset": map_off,\n        "sections": sections,\n        "counts": {s: v["count"] for s, v in sections.items()},\n        "map_confirmed": confirmed,\n        "obfuscation_signal": {\n            "short_names_pct": round(100.0 * short / max(1, len(lens)), 1),\n            "note": "short names plus high type counts usually means an obfuscator ran"},\n        "name_roots": roots[:30],\n        "sample_strings": strings[:40],\n    }\n\n\ndef axml(data, path):\n    """Android binary XML. Accepts the raw entry or an APK (finds AndroidManifest.xml)."""\n    if zipfile.is_zipfile(path):\n        try:\n            raw = zipfile.ZipFile(path).read("AndroidManifest.xml")\n        except KeyError:\n            return {"error": "AndroidManifest.xml not in archive"}\n    else:\n        raw = data\n    ft, fhs = struct.unpack_from("<HH", raw, 0)\n    if ft != 0x0003:\n        return {"error": "not RES_XML style binary XML", "magic": raw[:4].hex()}\n    ct, chs, csize = struct.unpack_from("<HHI", raw, fhs)\n    # Chunk types are single uint16: string pool is 0x0001, not 0x0100. Verified.\n    if ct != 0x0001:\n        return {"error": "first chunk is not a string pool", "chunk": hex(ct)}\n    sc, stylec, flags, sstart, sstyles = struct.unpack_from("<IIIII", raw, fhs + 8)\n    idx, base = fhs + chs, fhs + sstart\n    strings = {}\n    for i in range(sc):\n        off = struct.unpack_from("<I", raw, idx + sstyles + i * 4)[0]\n        s = raw[base + off:]\n        if len(s) < 2:\n            continue\n        ln = struct.unpack_from("<H", s, 0)[0]\n        if flags & 0x100:\n            strings[i] = s[2:2 + ln].decode("utf-8", "replace")\n        else:\n            strings[i] = s[2:2 + ln * 2].decode("utf-16-le", "replace")\n    elements = []\n    pos = fhs + csize\n    while pos + 16 <= len(raw):\n        ctype, chs2, csize2 = struct.unpack_from("<HHI", raw, pos)\n        # body starts at the chunk header\'s end, which IS attrExt:\n        # line(4) comment(4) ns(4) name(4), then attrStart/attrSize/attrCount as uint16.\n        body = raw[pos + 8:pos + csize2]\n        if ctype in (0x0102, 0x0103) and len(body) >= 22:\n            line = struct.unpack_from("<I", body, 0)[0]\n            ns, name = struct.unpack_from("<II", body, 8)\n            attrStart, attrSize, attrCount = struct.unpack_from("<HHH", body, 16)\n            elements.append({"event": "start" if ctype == 0x0102 else "end",\n                             "name": strings.get(name, "?"), "ns": strings.get(ns, ""),\n                             "line": line, "offset": pos})\n            if ctype == 0x0102 and attrSize in (16, 20, 24) and attrCount:\n                # attrStart\'s base is not confirmed on every build. Probe the candidates and\n                # accept only a walk that lands exactly on the chunk end. Otherwise report the\n                # attributes as unverified instead of asserting values.\n                q = None\n                for base_off in (attrStart, attrStart + 8, attrStart + 12):\n                    if base_off + attrSize * attrCount == len(body):\n                        q = base_off\n                        break\n                if q is None:\n                    elements.append({"event": "attr_unverified", "name": strings.get(name, "?"),\n                                     "attrCount": attrCount, "offset": pos,\n                                     "note": "record base not confirmed; use apktool d -no-src"})\n                else:\n                    for k in range(attrCount):\n                        rec = body[q:q + attrSize]\n                        if len(rec) < 16:\n                            break\n                        nm, rawv, sz, dt, val = struct.unpack_from("<IIHBI", rec, 0)\n                        elements.append({"event": "attr", "name": strings.get(nm, "?"),\n                                         "value": strings.get(val, "") if dt == TYPE_STRING else val,\n                                         "dataType": hex(dt), "offset": pos,\n                                         "base_confirmed": True})\n                        q += attrSize\n        if csize2 <= 0:\n            break\n        pos += csize2\n    return {"file": path, "string_count": sc, "styles_count": stylec, "flags": hex(flags),\n            "utf8_strings": bool(flags & 0x100),\n            "strings_sample": [strings[i] for i in sorted(strings)][:60],\n            "elements": elements[:60],\n            "verified": "string pool and element names",\n            "unverified": "attribute record base (probed; check apktool d -no-src)"}\n\n\ndef apksig(data, path):\n    magic = b"APK Sig Block 42"\n    pos = data.rfind(magic)\n    if pos < 0:\n        return {"error": "no APK signing block (V2+); this may be V1-only",\n                "fallback": "jarsigner -verify -verbose <apk> is the real V1 tool"}\n    block_size = struct.unpack_from("<Q", data, pos - 8)[0]\n    region = data[pos - 8 - block_size:pos - 8]\n    out = {"file": path, "signing_block_offset": pos, "block_size": block_size,\n           "region_start": pos - 8 - block_size}\n    # AOSP pair = uint32 length, value of (length - 4) bytes, uint32 id. The length field\n    # includes the id. Accept a walk only when it lands exactly on the region end.\n    pairs, p, n = {}, 0, 0\n    while p + 8 <= len(region):\n        ln = struct.unpack_from("<I", region, p)[0]\n        if ln < 4 or p + 4 + ln > len(region):\n            break\n        aid = struct.unpack_from("<I", region, p + 4 + ln - 4)[0]\n        pairs[APK_SIG_IDS.get(aid, "id_%04x" % aid)] = ln - 4\n        p += 4 + ln\n        n += 1\n        if n > 40:\n            break\n    out["pairs"] = pairs\n    out["pairs_walked"] = n\n    out["walk_landed_at"] = p\n    out["walk_matched_region_end"] = (p == len(region))\n    if not out["walk_matched_region_end"]:\n        out["note"] = ("pair walk did not land on the region end, so the pair list is "\n                       "UNVERIFIED. The block itself is present (magic found). Confirm signer "\n                       "identity with jarsigner rather than trusting these offsets.")\n    cd = data.rfind(b"PK\\x05\\x06")\n    if cd >= 0:\n        out["central_directory_offset"] = cd\n        out["comment_len"] = struct.unpack_from("<H", data, cd + 16)[0]\n    return out\n\n\ndef asar(data, path):\n    try:\n        size = struct.unpack_from("<I", data[4:8])[0]\n        hdr = json.loads(data[8:8 + size].decode("utf-8", "replace"))\n    except Exception as exc:\n        return {"error": "asar header unreadable: %s" % exc}\n    flat = {}\n\n    def walk(node, prefix=""):\n        if not isinstance(node, dict):\n            return\n        for k, v in (node.get("files") or {}).items():\n            p = prefix + "/" + k\n            if isinstance(v, dict):\n                if v.get("integrity"):\n                    flat[p] = {"offset": v.get("offset"), "size": v.get("size"),\n                               "integrity": v["integrity"]}\n                else:\n                    walk(v, p)\n\n    walk(hdr.get("header", hdr))\n    return {"file": path, "header_json_size": size, "entries": len(flat),\n            "package": flat.get("/package.json", {}), "sample": list(flat.items())[:30],\n            "integrity_blocks": sum(1 for v in flat.values() if v.get("integrity")),\n            "note": "unpacked .node addons live in app.asar.unpacked, not inside the archive"}\n\n\ndef hermes(data, path):\n    if data[:4] != b"\\xc6\\x1f\\xbc\\x03":\n        return {"error": "not Hermes bytecode"}\n    version, sig1, sig2, file_size, segment, offset = struct.unpack_from("<IIIIII", data, 4)\n    str_off, str_size = struct.unpack_from("<II", data, 44)\n    strings, p = [], str_off\n    while p + 4 <= str_off + str_size and len(strings) < 400:\n        n = struct.unpack_from("<I", data, p)[0]\n        if n == 0:\n            break\n        s = data[p + 4:p + 4 + n]\n        try:\n            strings.append(s.decode("utf-16-le", "replace"))\n        except Exception:\n            strings.append(s.hex())\n        p += 4 + n\n    return {"file": path, "header": {"magic": "c61fbc03", "version": version,\n            "segments_count": segment, "string_table_offset": str_off,\n            "string_table_size": str_size},\n            "strings": strings[:120], "string_count": len(strings),\n            "note": "the string pool is a separate section; code and strings decode separately"}\n\n\ndef pe(data, path):\n    try:\n        import pefile\n    except ImportError:\n        return {"error": "pefile not importable in this interpreter",\n                "fallback": "objdump -x <file>, or run this parser under the system Python"}\n    obj = pefile.PE(data=data)\n    out = {"file": path,\n          "machine": PE_MACHINES.get(obj.FILE_HEADER.Machine, hex(obj.FILE_HEADER.Machine)),\n          "subsystem": hex(getattr(obj.OPTIONAL_HEADER, "Subsystem", 0)),\n          "imagebase": hex(obj.OPTIONAL_HEADER.ImageBase),\n          "entry": hex(obj.OPTIONAL_HEADER.AddressOfEntryPoint),\n          "dll_characteristics": hex(getattr(obj.OPTIONAL_HEADER, "DllCharacteristics", 0)),\n          "data_directories": {PE_DATADIRS.get(i, "dir%d" % i): int(x[1])\n                               for i, x in enumerate(obj.NT_HEADERS.DEPTBL_TABLE) if x[1]}\n          if hasattr(obj.NT_HEADERS, "DEPTBL_TABLE") else {}}\n    if hasattr(obj, "DIRECTORY_ENTRY_IMPORT"):\n        out["imports"] = {e.dll.decode(errors="replace"): [\n            (getattr(g, "name", None) and g.name.decode(errors="replace")) or hex(g.ordinal)\n            for g in e.imports][:20] for e in obj.DIRECTORY_ENTRY_IMPORT}\n    for dir in obj.DIRECTORY_ENTRY_DEBUG or []:\n        try:\n            d = dir.entry\n            if d.Type == 2 and d.Data:\n                out["debug"] = {"type": "CODEVIEW",\n                                "pdb_path": d.Data.pdb.Name.decode(errors="replace").strip("\\0"),\n                                "age": d.Data.pdb.Age}\n        except Exception:\n            pass\n    return out\n\n\ndef macho(data, path):\n    out = {"file": path}\n    fat = {b"\\xca\\xfa\\xed\\xfe": ">U4", b"\\xcb\\xfa\\xed\\xfe": "<U4"}\n    if data[:4] in fat:\n        narch = struct.unpack_from(fat[data[:4]], data, 4)[0]\n        archs = []\n        for i in range(narch):\n            cputype, cpusubtype, off, size, align = struct.unpack_from(">5I", data, 8 + i * 20)\n            archs.append({"cputype": cputype, "cpusubtype": cpusubtype, "offset": off, "size": size})\n        out["fat"] = {"architectures": narch, "arch_list": archs}\n        if archs:\n            data = data[archs[0]["offset"]:archs[0]["offset"] + archs[0]["size"]]\n    if data[:4] not in (b"\\xfe\\xed\\xfa\\xcf", b"\\xfe\\xed\\xfa\\xce"):\n        return {"error": "not Mach-O after fat unwrap", "magic": data[:4].hex()}\n    be = data[:4] == b"\\xfe\\xed\\xfa\\xcf"\n    fmt = ">IIIIII" if be else "<IIIIII"\n    cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags = struct.unpack_from(fmt, data, 4)\n    out["header"] = {"cputype": cputype, "filetype": filetype, "ncmds": ncmds,\n                     "little_endian": not be}\n    p = 28 if be else 32\n    cmds, dylibs, segs = [], [], []\n    for _ in range(ncmds):\n        cmd, size = struct.unpack_from("<II", data, p)[:2] if not be else \\\n            struct.unpack_from(">II", data, p)[:2]\n        body = data[p:p + size]\n        if cmd in (0x1, 0x19):\n            segs.append(body[16:32].split(b"\\0")[0].decode(errors="replace"))\n        elif cmd in (0x6, 0x8, 0xc):\n            off, rest = struct.unpack_from("<II" if not be else ">II", body, 4)\n            dylibs.append(data[p + off:p + off + rest].split(b"\\0")[0].decode(errors="replace"))\n        elif cmd == 0x2e:\n            plat = struct.unpack_from("<I", body, 4)[0]\n            out["build_version"] = {"platform": MACHO_PLATFORMS.get(plat, plat),\n                                    "min": struct.unpack_from("<III", body, 8)}\n        elif cmd == 0x21:\n            base, sz = struct.unpack_from("<II" if not be else ">II", body, 4)\n            blob = data[p + base:p + base + sz]\n            out["code_signature"] = {"offset": p + base, "size": sz,\n                                     "blobs": parse_superblob(blob)}\n        cmds.append(MACHO_CMDS.get(cmd, hex(cmd)))\n        p += size\n    out["load_commands"] = cmds\n    out["segments"] = segs\n    out["dylibs"] = dylibs\n    return out\n\n\ndef parse_superblob(blob):\n    if blob[:4] != b"\\xcd\\xfa\\x85\\x30":\n        return {"error": "not a code-signing super blob", "magic": blob[:4].hex()}\n    length, count = struct.unpack_from(">II", blob, 8)\n    out = {"length": length, "blob_count": count, "blobs": []}\n    p = 16\n    for _ in range(count):\n        if p + 8 > len(blob):\n            break\n        typ, bsize = struct.unpack_from(">II", blob, p)\n        body = blob[p + 8:p + 8 + bsize]\n        entry = {"type": CS_BLOB_TYPES.get(typ, hex(typ)), "size": bsize, "offset": p}\n        if typ == 1:\n            try:\n                entry["entitlements"] = plistlib.loads(body)\n            except Exception:\n                entry["raw"] = body[:200].hex()\n        out["blobs"].append(entry)\n        p += 8 + bsize\n    return out\n\n\ndef wasm(data, path):\n    if data[:4] != b"\\x00\\x61\\x73\\x6d":\n        return {"error": "not WASM"}\n\n    def leb(buf, p):\n        val, shift = 0, 0\n        while p < len(buf):\n            b = buf[p]\n            val |= (b & 0x7f) << shift\n            p += 1\n            if not (b & 0x80):\n                break\n            shift += 7\n        return val, p\n\n    pos, out = 8, {"file": path, "sections": []}\n    while pos < len(data):\n        sid = data[pos]\n        size, pos = leb(data, pos + 1)\n        body = data[pos:pos + size]\n        entry = {"section": WASM_SECTIONS.get(sid, hex(sid)), "size": size}\n        if sid == 2:\n            n, q = leb(body, 0)\n            names = []\n            for _ in range(min(n, 60)):\n                m, q = leb(body, q)\n                mod = body[q:q + m].decode(errors="replace"); q += m\n                m2, q = leb(body, q)\n                names.append(mod + "." + body[q:q + m2].decode(errors="replace")); q += m2\n            entry["imports"] = names\n        elif sid == 7:\n            n, q = leb(body, 0)\n            names = []\n            for _ in range(min(n, 60)):\n                m, q = leb(body, q)\n                names.append(body[q:q + m].decode(errors="replace")); q += m\n                q += 1\n            entry["exports"] = names\n        elif sid == 0:\n            entry["name"] = body[:200].decode(errors="replace")\n        out["sections"].append(entry)\n        pos += size\n    return out\n\n\nTABLE = {"scan": scan, "dex": dex, "axml": axml, "apksig": apksig, "asar": asar,\n         "hermes": hermes, "pe": pe, "macho": macho, "wasm": wasm}\n\ndef main():\n    if len(sys.argv) < 3:\n        print("usage: parser.py <format> <file>")\n        return 2\n    fmt, path = sys.argv[1], sys.argv[2]\n    try:\n        data = open(path, "rb").read()\n    except Exception as exc:\n        print(json.dumps({"error": "read failed: %s" % exc}))\n        return 1\n    if fmt not in TABLE:\n        print(json.dumps({"error": "unknown format %s" % fmt, "available": sorted(TABLE)}))\n        return 2\n    print(json.dumps(TABLE[fmt](data, path), indent=1, default=str, ensure_ascii=False))\n    return 0\n\n\nif __name__ == "__main__":\n    sys.exit(main())\nfmt, path = sys.argv[1], sys.argv[2]\ntry:\n    data = open(path, "rb").read()\nexcept Exception as exc:\n    print(json.dumps({"error": "read failed: %s" % exc}))\n    sys.exit(1)\nif fmt not in TABLE:\n    print(json.dumps({"error": "unknown format %s" % fmt, "available": sorted(TABLE)}))\n    sys.exit(2)\nprint(json.dumps(TABLE[fmt](data, path), indent=1, default=str, ensure_ascii=False))\n'
GHIDRA_SCRIPT = '\n// leanrea Ghidra post-script: dump decompiled pseudocode to a file, not to the conversation.\n// Run with: analyzeHeadless <projdir> <projname> -import <file> -scriptPath <dir> \\\n//           -postScript DecompileAll.java -deleteProject\n//\n// Use fully-qualified names. `import ghidra.program.model.listing.Function` breaks this\n// build\'s script class lookup, and single-letter class names collide in ECJ.\n// 12.1.4 API confirmed by reflection: DecompileResults -> isValid(), getDecompiledFunction();\n// DecompiledFunction -> getC(). completed() and getDecomposedLayout() do not exist.\n// printError is not available here; use println.\n//@category leanrea\n\nimport java.io.PrintWriter;\n\npublic class DecompileAll extends ghidra.app.script.GhidraScript {\n\n    @Override\n    public void run() throws Exception {\n        String out = System.getenv("LEANREA_OUT");\n        if (out == null || out.isEmpty()) out = System.getProperty("user.dir") + "/decompile.txt";\n\n        ghidra.app.decompiler.DecompInterface decomp = new ghidra.app.decompiler.DecompInterface();\n        if (!decomp.openProgram(currentProgram)) {\n            println("decompiler open failed: " + decomp.getLastMessage());\n            return;\n        }\n\n        int ok = 0, failed = 0;\n        PrintWriter pw = new PrintWriter(out);\n        try {\n            for (ghidra.program.model.listing.Function f :\n                 currentProgram.getFunctionManager().getFunctions(true)) {\n                if (monitor.isCancelled()) break;\n                ghidra.app.decompiler.DecompileResults r = decomp.decompileFunction(f, 30, monitor);\n                if (r == null || !r.isValid()) { failed++; continue; }\n                ghidra.app.decompiler.DecompiledFunction df = r.getDecompiledFunction();\n                if (df == null) { failed++; continue; }\n                pw.println("=== " + f.getName() + " @ " + f.getEntryPoint() + " ===");\n                pw.println(df.getC());\n                ok++;\n            }\n        } finally {\n            pw.close();\n            decomp.dispose();\n        }\n        println("decompiled " + ok + " functions (" + failed + " failed) -> " + out);\n    }\n}\n'
BINPKG = {'jadx': {'brew': 'jadx', 'dnf': 'jadx', 'apt-get': 'jadx', 'pacman': 'jadx', 'zypper': 'jadx'}, 'apktool': {'brew': 'apktool', 'dnf': 'apktool', 'apt-get': 'apktool', 'pacman': 'apktool', 'zypper': 'apktool'}, 'r2': {'brew': 'radare2', 'dnf': 'radare2', 'apt-get': 'radare2', 'pacman': 'radare2', 'zypper': 'radare2'}, 'binwalk': {'brew': 'binwalk', 'dnf': 'binwalk', 'apt-get': 'binwalk', 'pacman': 'binwalk', 'zypper': 'binwalk'}, 'wasm-objdump': {'brew': 'wabt', 'dnf': 'wabt', 'apt-get': 'wabt', 'pacman': 'wabt', 'zypper': 'wabt'}, 'tshark': {'brew': 'wireshark', 'dnf': 'wireshark', 'apt-get': 'tshark', 'pacman': 'tshark', 'zypper': 'wireshark'}, 'analyzeHeadless': {'brew': 'ghidra', 'dnf': 'ghidra', 'apt-get': 'ghidra', 'pacman': 'ghidra', 'zypper': 'ghidra'}, 'plistutil': {'brew': 'libplist', 'dnf': 'libplist', 'apt-get': 'libplist-utils', 'pacman': 'libplist', 'zypper': 'libplist'}, 'go': {'brew': 'go', 'dnf': 'golang', 'apt-get': 'golang', 'pacman': 'go', 'zypper': 'go'}, 'cargo': {'brew': 'rust', 'dnf': 'cargo', 'apt-get': 'cargo', 'pacman': 'rust', 'zypper': 'cargo'}, 'dotnet': {'brew': 'dotnet', 'dnf': 'dotnet-sdk', 'apt-get': 'dotnet-sdk-8.0', 'pacman': 'dotnet-sdk', 'zypper': 'dotnet-sdk'}, 'npx': {'brew': 'node', 'dnf': 'nodejs', 'apt-get': 'nodejs', 'pacman': 'nodejs', 'zypper': 'nodejs'}, 'jq': {'brew': 'jq', 'dnf': 'jq', 'apt-get': 'jq', 'pacman': 'jq', 'zypper': 'jq'}, '7z': {'brew': 'p7zip', 'dnf': 'p7zip', 'apt-get': 'p7zip-full', 'pacman': 'p7zip', 'zypper': 'p7zip'}, 'unzip': {'brew': 'unzip', 'dnf': 'unzip', 'apt-get': 'unzip', 'pacman': 'unzip', 'zypper': 'unzip'}, 'rg': {'brew': 'ripgrep', 'dnf': 'ripgrep', 'apt-get': 'ripgrep', 'pacman': 'ripgrep', 'zypper': 'ripgrep'}, 'cpio': {'brew': 'cpio', 'dnf': 'cpio', 'apt-get': 'cpio', 'pacman': 'cpio', 'zypper': 'cpio'}}
PIP_ONLY = {'unblob': 'unblob', 'pwn': 'pwntools', 'pefile': 'pefile', 'capstone': 'capstone', 'elftools': 'pyelftools', 'hbc-decompiler': 'hermes-dec', 'hbc-disassembler': 'hermes-dec'}
TOOL_NAMES = ['file', 'strings', 'rg', 'nm', 'objdump', 'readelf', 'binwalk', 'unblob', 'jadx', 'apktool', 'r2', 'rabin2', 'radiff2', 'rafind2', 'tshark', 'wasm-objdump', 'wasm2wat', 'wasm-decompile', 'hbc-decompiler', 'sqlite3', 'plistutil', 'analyzeHeadless', 'pwn', 'go', 'cargo', 'ilspycmd', 'cargo-audit', 'java', 'node', 'npx', 'jq', 'grep', '7z', 'unzip', 'cpio', 'ar', 'lipo', 'otool', 'codesign', 'plutil', 'assetutil', 'jarsigner', 'dexdump']
ROUTER = {'electron': {'name': 'Electron / asar', 'detect': '*.app/Contents/Resources/app.asar, or asar header JSON', 'fastest': 'npx -y @electron/asar extract app.asar X; app.asar.unpacked/ holds the .node addons; entry point is package.json main', 'needs': ['npx', 'jq', 'rg'], 'fallback': {'npx': 'read the asar header JSON directly (parser.py asar), then slice by offset', 'rg': 'grep -r with --include for dot folders'}, 'traps': ['bundlers hide code in dot folders (.webpack/, .vite/): search with rg -uu', "app-specific IPC wrappers hide the real channel names, so also search quoted 'a:b' literals", 'asar integrity checks can block tooling; extract rather than trust the manifest', 'fuses decide whether --inspect works: npx -y @electron/fuses read --app A.app'], 'first_move': 'X=$ROOT/extracted/electron; npx -y @electron/asar extract \'$T\' $X; jq \'{name,version,main}\' $X/package.json; echo \'--- SURFACE\'; rg -uu -n -o --glob \'!node_modules\' \'.{0,120}(ipcMain\\.(handle|handleOnce|on|once)|ipcRenderer\\.(invoke|send|sendSync|on)|contextBridge\\.exposeIn(Main|Isolated)World|webPreferences|preload:|nodeIntegration|contextIsolation|sandbox|\\.node\\b).{0,160}\' $X | head -60; echo \'--- CHANNELS\'; rg -uu -n -o \'"[a-z]+:[a-z0-9:-]+"\' $X | sort -u | head -40'}, 'web_bundle': {'name': 'minified web bundle / source maps', 'detect': 'sourceMappingURL present, or minified JS', 'fastest': 'look for sourceMappingURL first; if the map has sourcesContent the original source is already there', 'needs': ['rg', 'jq'], 'fallback': {'rg': 'grep -o with a bounded window', 'jq': 'python3 -c json load'}, 'traps': ['never cat a minified file; slice around the needle', 'webpack module ids are numeric, so map the id to the source before reading', 'prettier --parser babel makes a readable file, then read by line range'], 'first_move': "rg -uu -n 'sourceMappingURL' '$T' | head; echo '--- SLICE'; rg -uu -n -o '.{0,200}NEEDLE.{0,200}' '$T' | head -20"}, 'hermes': {'name': 'Hermes / bytenode / V8 snapshot', 'detect': 'magic c6 1f bc 03 (index.android.bundle)', 'fastest': 'hbc-file-parser, hbc-decompiler, hbc-disassembler', 'needs': ['hbc-decompiler'], 'fallback': {'hbc-decompiler': 'parser.py hermes reads the header and string pool; .jsc from bytenode cannot be decompiled, use strings plus runtime hooks'}, 'traps': ['the string pool is a separate section, so code and strings decode independently', 'hermes-dec reads the bytecode version from the header; older versions misparse'], 'first_move': "hbc-file-parser '$T'; echo '--- DECODE'; hbc-decompiler '$T' > $ROOT/dis/hermes.txt 2>&1; head -40 $ROOT/dis/hermes.txt"}, 'macho': {'name': 'Mach-O', 'detect': 'file -b: Mach-O, or fat magic ca fa ed fe', 'fastest': 'lipo -info, then nm -u (the capability map), otool -L, strings; otool -ov for ObjC classes', 'needs': ['lipo', 'otool', 'nm', 'codesign', 'strings'], 'fallback': {'lipo': 'parser.py macho unwraps the fat archive', 'otool': 'rabin2 -ES, or parser.py macho for load commands', 'codesign': 'parser.py macho reads the code-signing super blob and entitlements', 'nm': 'readelf or rabin2 -E for ELF-equivalent symbol tables'}, 'traps': ['fat binaries need lipo -thin first', 'otool -tV annotates the literal pool, which is how you find who uses a string', 'entitlements come from the code signature, not the file contents'], 'first_move': "file -b '$T'; lipo -info '$T' 2>/dev/null; otool -L '$T' 2>/dev/null | head -40; echo '--- LOAD COMMANDS'; $PY '$PARSER' macho '$T'; echo '--- STRINGS'; strings -a -n 6 '$T' | rg -v '^[_$.]' | head -200"}, 'native_logic': {'name': 'native code logic', 'detect': 'native Mach-O/ELF/PE when a specific function matters', 'fastest': 'r2 pseudocode for one function; Ghidra headless when real pseudocode is needed', 'needs': ['r2', 'analyzeHeadless'], 'fallback': {'r2': 'objdump -d -M intel dumped to a file, then grep that file', 'analyzeHeadless': 'r2 af+a pdf, or read the call/string trail with objdump'}, 'traps': ['read the call and string trail, not every instruction', 'a C++ name in a bl target already tells you the protocol (getpeereid, write, hash)', 'dump the disassembly once to a file, then grep that file; do not re-run per query'], 'first_move': "r2 -qc 'aaa; afl; pdf @ main' '$T' 2>/dev/null | head -120"}, 'elf_pe': {'name': 'ELF / PE', 'detect': 'magic 7f 45 4c 46, or MZ', 'fastest': 'readelf -hlSW plus readelf -dW (NEEDED is the capability map), nm -D --defined-only, objdump -d -M intel; PE: objdump -x', 'needs': ['readelf', 'nm', 'objdump', 'pwn', 'strings'], 'fallback': {'readelf': 'rabin2 -ESZ', 'pwn': 'checksec values can be read from DllCharacteristics / ELF headers directly', 'objdump': "r2 -qc 'aaa; afl'"}, 'traps': ['nm on a stripped binary still works through .dynsym; go tool nm survives stripping', 'DT_NEEDED is the capability map and is cheaper than any disassembly', 'PIE/RELRO/canary/FORTIFY give the hardening story with no code reading', 'the PE resource section often holds the real version strings and manifest; the PDB debug path leaks the build path'], 'first_move': "file -b '$T'; readelf -hlS '$T' 2>/dev/null | head -50; echo '--- NEEDED'; readelf -dW '$T' 2>/dev/null | rg 'NEEDED|RPATH|RUNPATH'; echo '--- EXPORTS'; nm -D --defined-only '$T' 2>/dev/null | head -60; echo '--- CHECKSEC'; pwn checksec '$T' 2>/dev/null; echo '--- STRINGS'; strings -a -n 6 '$T' | rg -v '^[_$.]' | head -200"}, 'go': {'name': 'Go buildinfo', 'detect': '__go_buildinfo or .go.buildinfo section', 'fastest': 'go version -m answers toolchain, path, module, deps and build flags in one call', 'needs': ['go'], 'fallback': {'go': "strings -a | rg -m1 -A200 'Go buildinf'"}, 'traps': ['.go.buildinfo and .go.modinfo are sections, so stripping may still leave them', 'Rust is not Go: look for /rustc/<hash> and the crate-version path pattern'], 'first_move': "go version -m '$T'; echo '--- SYMBOLS'; go tool nm '$T' 2>/dev/null | rg 'main\\.' | head -60"}, 'rust': {'name': 'Rust', 'detect': '/rustc/<40 hex> in strings, or .dep-v0 section', 'fastest': 'cargo audit bin T for cargo-auditable deps; the compiler hash comes from strings', 'needs': ['cargo-audit', 'strings'], 'fallback': {'cargo-audit': '.dep-v0 is deflate-compressed, so zlib-inflate it; raw strings will not show it', 'strings': 'crates come from path patterns name-x.y.z/src, not symbol names'}, 'traps': ['crates come from path patterns name-x.y.z/src, not from symbol names', '.dep-v0 is deflate-compressed, so raw strings will not show it'], 'first_move': "strings -a '$T' | rg -o '/rustc/[0-9a-f]{40}' | sort -u; echo '--- CRATES'; strings -a '$T' | rg -o '[a-z0-9_-]+-[0-9]+\\.[0-9]+\\.[0-9]+/src' | sort -u | head -60"}, 'dotnet': {'name': '.NET', 'detect': 'file -b: Mono/.Net assembly', 'fastest': 'ilspycmd -p -o X T.dll then search', 'needs': ['ilspycmd', 'rg'], 'fallback': {'ilspycmd': 'fails on older SDKs with a NuGet settings-format error; check the SDK version, or read metadata with python pefile-free tools'}, 'traps': ['P/Invoke shows up as DllImport, which is the native boundary', 'NativeAOT and IL2CPP are native; use the native rows instead'], 'first_move': "ilspycmd -p -o $ROOT/extracted/dn '$T' 2>&1 | head -5; rg 'DllImport' $ROOT/extracted/dn | head -40"}, 'apk': {'name': 'Android APK', 'detect': 'zip containing AndroidManifest.xml', 'fastest': 'jadx -d X --show-bad-code for code; apktool d --no-src for manifest and resources only', 'needs': ['jadx', 'apktool', 'unzip', 'rg'], 'fallback': {'jadx': 'parser.py dex gives header counts and the obfuscation signal without decompiling', 'apktool': 'parser.py axml decodes the binary XML string pool'}, 'traps': ['lib/*/libflutter.so means Flutter (Dart AOT: blutter); libhermes.so means React Native', 'AndroidManifest.xml inside the APK is binary XML, not text', 'the APK signing block sits after the central directory, so unzip -l will not show it', 'obfuscated packages hide intent in string literals and annotation names'], 'first_move': "unzip -l '$T' | head -40; echo '--- MANIFEST'; $PY '$PARSER' axml '$T'; echo '--- SIGNING'; $PY '$PARSER' apksig '$T'; echo '--- NATIVE'; unzip -l '$T' | rg '\\\\.so$' | head -20"}, 'dex': {'name': 'DEX', 'detect': 'magic dex\\n03', 'fastest': 'jadx for decompiled Java; the header map gives counts before decompiling', 'needs': ['jadx'], 'fallback': {'jadx': "parser.py dex reads the file's own map_list for section counts and name roots"}, 'traps': ['counts come from the map_list, so inventory is cheap before decompiling', 'very short names plus high counts usually mean an obfuscator ran'], 'first_move': "$PY '$PARSER' dex '$T'; echo '--- NAMES'; strings -a '$T' | rg -v '^[_$.]' | head -120"}, 'flutter': {'name': 'Flutter snapshot hash', 'detect': 'magic f5 f5 dc dc inside libapp.so', 'fastest': 'the 32 hex chars at +20 are the snapshot hash; map it to a Dart version', 'needs': ['strings'], 'fallback': {'blutter': 'not present on this machine; the hash plus the string pool answers most naming questions'}, 'traps': ['libapp.so is Dart AOT, not Hermes; do not route it through the Hermes row'], 'first_move': "$PY '$PARSER' scan '$T'; echo '--- STRINGS'; strings -a -n 6 '$T' | head -120"}, 'wasm': {'name': 'WASM', 'detect': 'magic 00 61 73 6d', 'fastest': 'wasm-objdump -x for structure, wasm-decompile for logic, wasm2wat for exact semantics', 'needs': ['wasm-objdump', 'wasm-decompile', 'wasm2wat'], 'fallback': {'wasm-objdump': 'parser.py wasm walks the section table with LEB128'}, 'traps': ['the name section is optional; a stripped WASM has no function names', 'imports are the host boundary and usually answer what the module can do'], 'first_move': "wasm-objdump -x '$T' | head -60; echo '--- DECOMPILED'; wasm-decompile '$T' 2>/dev/null | head -80"}, 'sqlite': {'name': 'SQLite', 'detect': 'magic SQLite format 3', 'fastest': 'copy the db plus the -wal file if present, then open read-only', 'needs': ['sqlite3'], 'fallback': {'sqlite3': 'parser.py scan confirms the page layout; python sqlite3 is in stdlib'}, 'traps': ['the -wal holds rows the main file does not', 'open with mode=ro so the target is never modified'], 'first_move': 'cp \'$T\' $ROOT/work/ 2>/dev/null; sqlite3 \'file:\'"\'$T\'"\'?mode=ro\' \'.schema\' | head -80'}, 'pcap_har': {'name': 'pcap / HAR', 'detect': 'pcap/pcapng magic, or HAR JSON', 'fastest': 'tshark conversations first, then fields for DNS names, TLS SNI and full URIs', 'needs': ['tshark', 'jq'], 'fallback': {'tshark': 'pcapng and pcap differ in field names even though tshark reads both; without tshark, read the HAR entries with jq'}, 'traps': ['SNI identifies the peer without decrypting anything', 'HAR: jq -r \'.log.entries[].request | .method+" "+.url\''], 'first_move': "tshark -r '$T' -q -z conv,tcp 2>&1 | head -40; echo '--- NAMES'; tshark -r '$T' -T fields -e dns.qry.name -e tls.handshake.extensions_server_name -e http.request.full_uri 2>/dev/null | sort -u | head -60"}, 'plist': {'name': 'plist / nib / car', 'detect': 'bplist00 or <?xml', 'fastest': 'plutil -p F, or plistlib for structured access', 'needs': ['plutil', 'plistutil'], 'fallback': {'plutil': 'plistutil -i F -o - works on Linux; python plistlib is in stdlib', 'assetutil': 'not available on Linux; asset catalogs cannot be decoded here'}, 'traps': ['Info.plist holds the bundle id, URL schemes and the privacy strings'], 'first_move': "plistutil -i '$T' -o - 2>/dev/null | head -60"}, 'firmware': {'name': 'Firmware', 'detect': 'filesystem/compressed container signatures', 'fastest': 'unblob -e X F (signature-aware), binwalk -e F as the fallback', 'needs': ['unblob', 'binwalk'], 'fallback': {'unblob': 'binwalk -e, then route every extracted binary back through this table', 'binwalk': 'parser.py scan gives a signature map with offsets'}, 'traps': ['then route every extracted binary back through this table', 'overlapping signatures give false positives; verify offsets before trusting a hit'], 'first_move': "unblob -e $ROOT/extracted/unblob '$T' 2>&1 | head -30; echo '--- SIGNATURES'; $PY '$PARSER' scan '$T'"}, 'website': {'name': 'Website', 'detect': 'URL rather than a file', 'fastest': 'curl -sL for HTML and script srcs, then fetch the JS and slice it', 'needs': ['curl'], 'fallback': {'curl': 'fetch the page and read the script srcs', 'headless browser': "Playwright page.on('request') or Chrome --remote-debugging-port for runtime behavior"}, 'traps': ['runtime behavior needs a headless browser with a network log; static HTML is not execution'], 'first_move': 'curl -sL \'$T\' | rg -o \'src="[^"]+"\' | head -40'}, 'runtime': {'name': 'Running app / runtime', 'detect': 'a live process or device rather than a file', 'fastest': 'adb logcat for Android, frida for hooks, strace for syscalls on Linux; lldb -p PID or dtrace on macOS', 'needs': ['adb', 'frida', 'strace'], 'fallback': {'strace': 'static reading only; label runtime findings separately', 'frida': 'install frida-tools; or read logs without hooks'}, 'traps': ['static reading is not observed execution; label runtime findings separately', 'Electron --inspect only works if the RunAsNode/inspect fuses allow it'], 'first_move': "echo 'runtime: adb -s <dev> logcat -d | head, or strace -f -e trace=network <cmd>'"}, 'generic': {'name': 'unclassified', 'detect': 'no rule matched', 'fastest': 'file -b, then the cheapest inventory, then route the result through this table', 'needs': ['file', 'strings'], 'fallback': {'file': 'read magic bytes directly'}, 'traps': ['do not assume the container format; read magic bytes first'], 'first_move': "file -b '$T'; echo '--- STRINGS'; strings -a -n 6 '$T' | rg -v '^[_$.]' | head -120"}}
MAGIC = [(b'\x7fELF', 'elf_pe'), (b'MZ', 'elf_pe'), (b'\xca\xfa\xed\xfe', 'macho'), (b'\xcb\xfa\xed\xfe', 'macho'), (b'\xfe\xed\xfa\xcf', 'macho'), (b'\xfe\xed\xfa\xce', 'macho'), (b'\x00asm', 'wasm'), (b'\xc6\x1f\xbc\x03', 'hermes'), (b'SQLite format 3\x00', 'sqlite'), (b'bplist00', 'plist'), (b'<?xml', 'plist'), (b"7z\xbc\xaf'\x1c", 'firmware'), (b'(\xb5/\xfd', 'firmware'), (b'\x1f\x8b', 'firmware'), (b'\xfd7zXZ\x00', 'firmware'), (b'\xed\xab\xee\xdb', 'firmware'), (b'\xd0\r\xfe\xed', 'firmware'), (b'hsqs', 'firmware'), (b'070701', 'firmware'), (b'dex\n03', 'dex'), (b'\xf5\xf5\xdc\xdc', 'flutter'), (b'PK\x03\x04', 'zip')]

def hashlib_sha1(path: str) -> str:
    import hashlib
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        h.update(f.read(400000))
    return h.hexdigest()[:16]

class Tools:

    class Valves(BaseModel):
        ROOT: str = ''
        SYSTEM_PYTHON: str = ''
        CONTEXT_CEILING: int = 256000
        MAX_OUTPUT_CHARS: int = 6000
        MAX_HEAD_LINES: int = 120
        COMMAND_TIMEOUT: int = 120
        ALLOW_INSTALL: bool = True
        AUTHORIZATION: str = 'authorized - owned or permitted target, for interoperability and security analysis'

    class UserValves(BaseModel):
        SUDO_PASSWORD: str = ''

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    def _root(self) -> str:
        r = self.valves.ROOT or os.environ.get('LEANREA_ROOT')
        if not r:
            r = os.path.join(os.environ.get('TMPDIR') or tempfile.gettempdir(), 're')
        return os.path.abspath(os.path.expanduser(r))

    def _scripts(self) -> str:
        d = os.path.join(self._root(), '_scripts')
        os.makedirs(d, exist_ok=True)
        java = os.path.join(d, 'DecompileAll.java')
        if not os.path.exists(java) or open(java, encoding='utf-8').read() != GHIDRA_SCRIPT:
            with open(java, 'w', encoding='utf-8') as f:
                f.write(GHIDRA_SCRIPT)
        return d

    def _parser(self) -> str:
        d = self._root()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, 'parser.py')
        if not os.path.exists(p) or open(p, encoding='utf-8').read() != PARSER_SRC:
            with open(p, 'w', encoding='utf-8') as f:
                f.write(PARSER_SRC)
        return p

    def _syspython(self) -> str:
        """Resolve the interpreter that can import pefile/capstone/elftools. The tool runs
        under a uv-cached Python that usually cannot, so this is probed, not assumed."""
        if self.valves.SYSTEM_PYTHON:
            return os.path.expanduser(self.valves.SYSTEM_PYTHON)
        for cand in ('/usr/bin/python3', '/usr/local/bin/python3', shutil.which('python3')):
            if cand and os.path.exists(cand):
                try:
                    r = subprocess.run([cand, '-c', "import pefile;print('ok')"], capture_output=True, timeout=30)
                    if r.stdout.decode().strip() == 'ok':
                        return cand
                except Exception:
                    pass
        return shutil.which('python3') or '/usr/bin/python3'

    def _which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def _slug(self, project: str) -> str:
        s = re.sub('[^a-z0-9._-]+', '_', (project or 'default').strip().lower()).strip('_')
        return s or 'default'

    def _pdir(self, project: str) -> str:
        base = os.path.join(self._root(), self._slug(project))
        for sub in ('work', 'out', 'extracted', 'dis'):
            os.makedirs(os.path.join(base, sub), exist_ok=True)
        return base

    def _state(self, project: str) -> dict:
        pdir = self._pdir(project)
        path = os.path.join(pdir, 'state.json')
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    st = json.load(f)
                st['project'] = self._slug(project)
                st['dir'] = pdir
                return st
            except Exception:
                pass
        return {'project': self._slug(project), 'dir': pdir, 'created': time.time(), 'goal': '', 'authorization': self.valves.AUTHORIZATION, 'target': {}, 'type': '', 'router': '', 'findings': [], 'questions': [], 'next': [], 'steps': 0, 'context': {'ceiling': int(self.valves.CONTEXT_CEILING), 'chars': 0}, 'machine': {'os': platform.system(), 'root': self._root(), 'syspython': self._syspython()}}

    def _save(self, project: str, st: dict) -> None:
        with open(os.path.join(st['dir'], 'state.json'), 'w', encoding='utf-8') as f:
            json.dump(st, f, indent=1, sort_keys=True)

    def _log(self, project: str, st: dict, action: str, detail: str, out_chars: int=0, status: str='ok') -> int:
        st['steps'] = int(st.get('steps', 0)) + 1
        seq = st['steps']
        rec = {'seq': seq, 'iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()), 'project': st['project'], 'action': action, 'status': status, 'detail': detail, 'out_chars': int(out_chars)}
        with open(os.path.join(st['dir'], 'log.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        md = os.path.join(st['dir'], 'log.md')
        new = not os.path.exists(md)
        with open(md, 'a', encoding='utf-8') as f:
            if new:
                f.write('# project %s\n\ngoal: %s\nauthorization: %s\nmachine: %s root: %s\n\n' % (st['project'], st.get('goal') or '-', st.get('authorization', '-'), st.get('machine', {}).get('os', '-'), st.get('machine', {}).get('root', '-')))
            f.write('## step %d  %s  [%s/%s] %s\n%s\n\n' % (seq, rec['iso'], action, status, (detail or '-').strip(), ''))
        st['context']['chars'] = int(st['context'].get('chars', 0)) + int(out_chars)
        return seq

    def _rel(self, st: dict, path: str) -> str:
        return path.replace(st['dir'], '...')

    def _budget(self, st: dict) -> str:
        ceiling = int(st['context']['ceiling'])
        used = int(st['context'].get('chars', 0))
        pct = 100.0 * used / max(1, ceiling)
        if pct > 90:
            return '\nbudget: %d of %d (%.0f%%) - handoff() now and resume() in a fresh window' % (used, ceiling, pct)
        if pct > 70:
            return '\nbudget: %d of %d (%.0f%%) - trim output, stop re-verifying' % (used, ceiling, pct)
        return ''

    def _cap(self, st: dict, text: str) -> str:
        limit = int(self.valves.MAX_OUTPUT_CHARS)
        out = text if len(text) <= limit else text[:limit] + '\n[capped %d chars - full output saved, use outgrep]' % (len(text) - limit)
        return out + self._budget(st)

    def _run(self, project: str, st: dict, command: str, note: str='', lines: int=0, env_extra: Optional[dict]=None) -> str:
        bad = re.search('\\b(rm\\s+-rf\\s+/|mkfs|dd\\s+if=|shutdown|reboot|>\\s*/dev/sd)\\b', command)
        if bad:
            self._log(project, st, 'blocked', command, status='refused')
            self._save(project, st)
            return 'Refused: %s is destructive. Writes belong inside the project work dir.' % bad.group(0)
        env = dict(os.environ)
        env['ROOT'] = st['dir']
        env['T'] = st.get('target', {}).get('path', '')
        env['PY'] = self._syspython()
        env['PARSER'] = self._parser()
        if env_extra:
            env.update(env_extra)
        try:
            proc = subprocess.run(['/bin/bash', '-c', command], cwd=st['dir'], capture_output=True, timeout=int(self.valves.COMMAND_TIMEOUT), env=env)
        except subprocess.TimeoutExpired:
            self._log(project, st, 'run', command, status='timeout')
            self._save(project, st)
            return 'TIMEOUT after %ds\n%s' % (int(self.valves.COMMAND_TIMEOUT), note)
        raw = proc.stdout.decode('utf-8', 'replace')
        if proc.stderr:
            raw += '\n[stderr]\n' + proc.stderr.decode('utf-8', 'replace')
        seq = self._log(project, st, 'run', command, out_chars=min(len(raw), int(self.valves.MAX_OUTPUT_CHARS)))
        outfile = os.path.join(st['dir'], 'out', '%03d.txt' % seq)
        with open(outfile, 'w', encoding='utf-8') as f:
            f.write('$ %s\n%s\n' % (command, raw))
        body = '\n'.join(raw.splitlines()[:lines or int(self.valves.MAX_HEAD_LINES)])
        head = 'step %d | exit %d | full output %s\n' % (seq, proc.returncode, self._rel(st, outfile))
        self._save(project, st)
        return self._cap(st, head + body)

    def _manager(self) -> Optional[str]:
        for pm in ('brew', 'dnf', 'apt-get', 'pacman', 'zypper'):
            if self._which(pm):
                return pm
        return None

    def _sudo(self, cmd: str) -> str:
        pw = getattr(self.user_valves, 'SUDO_PASSWORD', '')
        if pw:
            return "printf '%s\n' | sudo -S %s" % (pw.replace("'", "'\\''"), cmd)
        return 'sudo -S %s' % cmd

    def toolchain(self, project: str='') -> str:
        """Report which RE tools resolve, which are missing, and what the fallback is."""
        st = self._state(project)
        have = {n: self._which(n) for n in TOOL_NAMES}
        present = sorted([k for k, v in have.items() if v])
        missing = sorted([k for k, v in have.items() if not v])
        out = ['leanrea toolchain', 'machine: %s' % platform.system(), 'root: %s' % self._root(), 'system python (can import pefile): %s' % self._syspython(), 'package manager: %s' % (self._manager() or 'none found'), 'context ceiling: %d tokens' % int(self.valves.CONTEXT_CEILING), 'present (%d): %s' % (len(present), ', '.join(present)), 'missing (%d): %s' % (len(missing), ', '.join(missing) or 'none'), '', 'A missing tool is not a blocker. Each router row carries a fallback, and ensure() installs only what the detected row needs when ALLOW_INSTALL is on.']
        self._log(project, st, 'toolchain', '%d present, %d missing' % (len(present), len(missing)), out_chars=len('\n'.join(out)))
        self._save(project, st)
        return '\n'.join(out)

    def install(self, project: str, tools: str='', target: str='') -> str:
        """Install RE tools. Pass a space or comma separated list. With no list, install what
        the detected router row for the target needs. Reports the command when ALLOW_INSTALL is
        off rather than running it."""
        st = self._state(project)
        names = [x.strip() for x in re.split('[,\\s]+', tools or '') if x.strip()]
        if not names:
            src = target or st.get('target', {}).get('path')
            if not src:
                return 'Pass tools, or set a target so ensure() can resolve what the row needs.'
            names = ROUTER.get(self._detect(src), ROUTER['generic']).get('needs', [])
        pm = self._manager()
        sp = self._syspython()
        actions, skipped, failed = ([], [], [])
        for name in names:
            if self._which(name):
                skipped.append('%s already present at %s' % (name, self._which(name)))
                continue
            if not self.valves.ALLOW_INSTALL:
                actions.append('%s: not installed (ALLOW_INSTALL off). Fallback: %s' % (name, ROUTER.get(st.get('type') or 'generic', ROUTER['generic']).get('fallback', {}).get(name, 'read it with the parsers or an existing CLI')))
                continue
            if name in PIP_ONLY:
                cmd = '%s -m pip install --user %s' % (sp, PIP_ONLY[name])
            elif name == 'ilspycmd':
                cmd = 'dotnet tool install -g ilspycmd'
            elif name == 'cargo-audit':
                cmd = 'cargo install cargo-audit'
            elif pm and name in BINPKG:
                cmd = self._sudo('%s install -y %s' % (pm, BINPKG[name][pm])) if pm != 'brew' else 'brew install %s' % BINPKG[name]['brew']
            else:
                actions.append('%s: no package mapping for %s on %s' % (name, name, pm or 'no package manager'))
                continue
            try:
                proc = subprocess.run(['/bin/bash', '-c', cmd], capture_output=True, timeout=int(self.valves.COMMAND_TIMEOUT) * 3)
                ok = proc.returncode == 0
                tail = (proc.stdout + proc.stderr).decode('utf-8', 'replace').splitlines()[-3:]
                self._log(project, st, 'install', cmd, status='ok' if ok else 'failed', out_chars=sum((len(x) for x in tail)))
                if ok and self._which(name):
                    actions.append('%s installed: %s' % (name, self._which(name)))
                elif ok:
                    actions.append('%s ran but %s still not on PATH (check ~/.local/bin or ~/.dotnet/tools)' % (name, name))
                else:
                    failed.append('%s: %s' % (name, ' '.join(tail)))
            except Exception as exc:
                self._log(project, st, 'install', cmd, status='error')
                failed.append('%s: %s' % (name, exc))
        out = ['install for %s' % st['project'], 'manager: %s' % (pm or 'none'), '', 'actions:', '\n  '.join(actions or ['  (none)']), 'already present:', '\n  '.join(skipped or ['  (none)']), 'failed:', '\n  '.join(failed or ['  (none)']), '', 'Re-run toolchain() to confirm, then route() again.']
        self._log(project, st, 'install', tools or 'router needs', out_chars=len('\n'.join(out)))
        self._save(project, st)
        return self._cap(st, '\n'.join(out))

    def ensure(self, project: str, target: str='', auto: bool=True) -> str:
        """Check the detected row's tools. Install what is missing if allowed, otherwise give
        the fallback in the same row. A missing tool is never treated as a blocker."""
        st = self._state(project)
        src = target or st.get('target', {}).get('path')
        if not src:
            return 'No target. Run plan() or pass target.'
        kind = self._detect(src)
        row = ROUTER.get(kind, ROUTER['generic'])
        st['type'] = kind
        st['router'] = row['name']
        missing = [n for n in row.get('needs', []) if not self._which(n)]
        out = ['route: %s (%s)' % (row['name'], row['detect']), 'needs: %s' % ', '.join(row.get('needs', [])), 'missing: %s' % (', '.join(missing) or 'none')]
        if missing:
            if auto and self.valves.ALLOW_INSTALL:
                out.append('\ninstalling:')
                out.append(self.install(project, tools=' '.join(missing)))
                still = [n for n in missing if not self._which(n)]
                out.append('\nstill missing after install: %s' % (', '.join(still) or 'none'))
                for n in still:
                    fb = row.get('fallback', {}).get(n)
                    if fb:
                        out.append('  fallback for %s: %s' % (n, fb))
            else:
                out.append('\nfallbacks in this row:')
                for n in missing:
                    out.append('  %s: %s' % (n, row.get('fallback', {}).get(n, 'no documented fallback')))
            out.append('\nfirst move unchanged: ' + row['first_move'].replace('$T', src).replace('$ROOT', st['dir']).replace('$PY', self._syspython()).replace('$PARSER', self._parser()))
        else:
            out.append('\nall row tools resolve. first move: ' + row['first_move'].replace('$T', src).replace('$ROOT', st['dir']).replace('$PY', self._syspython()).replace('$PARSER', self._parser()))
        self._log(project, st, 'ensure', 'missing: %s' % (', '.join(missing) or 'none'), out_chars=len('\n'.join(out)))
        self._save(project, st)
        return self._cap(st, '\n'.join(out))

    def rules(self, project: str='') -> str:
        """Reload leanrea's operating rules. Cheap way to restore discipline in a fresh context."""
        st = self._state(project)
        lines = ['leanrea rules (mirrors SKILL.md)', '1. Work on a copy (cp -c on APFS is instant). Put each extraction in its own dir under the root. Reuse an extraction if one exists.', '2. Route by file -b and the magic bytes first. Run the cheapest inventory next. Only then decompile or read code.', '3. rg skips hidden and ignored files, and bundlers hide code (.webpack/, .vite/). Always search extracted trees with rg -uu.', "4. Never cat a big or minified file. Slice it: rg -o '.{0,200}NEEDLE.{0,200}' F | head. Dump a disassembly once to a file, then grep that file.", "5. Batch independent probes into ONE shell call with ; and quoted echo '--- name' markers. Cap every output with head or -m.", '6. Answer from evidence. Mark each claim seen (cite file:offset, or a symbol/string) or inferred. A fact you did not find is unknown, not absent. Static reading is not observed execution.', '7. A missing tool is not a blocker: use the fallback in the same row, or install it if the user allows it.', '8. Turns cost more than bytes. Aim for 2-4 calls: one batched recon, one or two targeted slices, then answer. Stop when every item asked has evidence. Do not re-verify what you already saw.', '', "Speed patterns: strings and imports first (nm -u + strings + exports usually answers 'what does it do' with no disassembly). To find who uses a string, get its address from the otool literal pool comments or r2, then the enclosing function label. A C++ name in a bl target already names the protocol. For JS, go from the native/JS boundary inward. Comparing versions: diff the inventories before any code. Rebuilding: minimal reimplementation, then test same inputs against the original.", '', 'Durability: every step is logged per project. If the process dies or context runs out, resume() rebuilds it from disk.']
        self._log(project, st, 'rules', 'rules reloaded', out_chars=len('\n'.join(lines)))
        self._save(project, st)
        return '\n'.join(lines)

    def plan(self, project: str, goal: str='', target: str='', authorization: str='', questions: str='', next_action: str='') -> str:
        """Open or update a project: goal, target, authorization basis, open questions, next actions."""
        st = self._state(project)
        if goal:
            st['goal'] = goal
        if authorization:
            st['authorization'] = authorization
        if target and os.path.exists(target):
            st['target'] = {'path': os.path.abspath(os.path.expanduser(target)), 'size': os.path.getsize(target), 'sha1': hashlib_sha1(target)}
            st['type'] = self._detect(target)
            st['router'] = ROUTER.get(st['type'], ROUTER['generic'])['name']
        for q in [x.strip() for x in (questions or '').split(';') if x.strip()]:
            st.setdefault('questions', []).append(q)
        for a in [x.strip() for x in (next_action or '').split(';') if x.strip()]:
            st.setdefault('next', []).append(a)
        out = ['project: %s' % st['project'], 'dir: %s' % st['dir'], 'goal: %s' % (st.get('goal') or '-'), 'authorization: %s' % st.get('authorization'), 'target: %s (%s, %d bytes)' % (st.get('target', {}).get('path', '-'), st.get('type', '-'), st.get('target', {}).get('size', 0)), 'route: %s' % (st.get('router') or '-'), '', 'open questions:', '\n  '.join(st.get('questions') or ['-']), 'next actions:', '\n  '.join(st.get('next') or ['-']), '', 'log: %s (step %d)' % (self._rel(st, os.path.join(st['dir'], 'log.md')), st['steps'])]
        self._log(project, st, 'plan', st.get('goal') or '-', out_chars=len('\n'.join(out)))
        self._save(project, st)
        return self._cap(st, '\n'.join(out))

    def route(self, target: str, project: str='') -> str:
        """Detect by magic bytes first, then the fastest path, the traps, and the first move."""
        st = self._state(project)
        target = os.path.expanduser(target)
        if not os.path.exists(target):
            return 'Target not found: %s' % target
        kind = self._detect(target)
        st['target'] = {'path': os.path.abspath(target), 'size': os.path.getsize(target), 'sha1': hashlib_sha1(target)}
        st['type'] = kind
        row = ROUTER.get(kind, ROUTER['generic'])
        st['router'] = row['name']
        first = row['first_move'].replace('$T', st['target']['path']).replace('$ROOT', st['dir']).replace('$PY', self._syspython()).replace('$PARSER', self._parser())
        out = ['target: %s (%d bytes)' % (st['target']['path'], st['target']['size']), 'detected: %s' % kind, 'route: %s' % row['name'], '', 'fastest path:', row['fastest'], '', 'tools this row needs:', ', '.join(row.get('needs', [])), 'fallbacks for missing tools:', '\n  '.join(('  %s: %s' % (k, v) for k, v in (row.get('fallback') or {}).items())) or '  -', '', 'traps:', '\n  '.join(row['traps']), '', 'first move (one batched call):', first, '', 'copy() then recon(). Never read the original in place.']
        self._log(project, st, 'route', '%s -> %s' % (kind, row['name']), out_chars=len('\n'.join(out)))
        self._save(project, st)
        return self._cap(st, '\n'.join(out))

    def copy(self, project: str, target: str='') -> str:
        """Copy the target into the project work dir so the original is never touched."""
        st = self._state(project)
        src = target or st.get('target', {}).get('path')
        if not src or not os.path.exists(src):
            return 'Nothing to copy. Pass target or run plan() first.'
        dst = os.path.join(st['dir'], 'work', os.path.basename(src))
        proc = subprocess.run(['cp', '-c', src, dst], capture_output=True, timeout=120)
        how = 'cp -c'
        if proc.returncode != 0:
            proc = subprocess.run(['cp', src, dst], capture_output=True, timeout=120)
            how = 'cp'
        self._log(project, st, 'copy', '%s -> %s (%s)' % (src, self._rel(st, dst), how))
        self._save(project, st)
        return 'copied to %s via %s' % (self._rel(st, dst), how)

    def recon(self, project: str, target: str='', focus: str='') -> str:
        """Run the single batched inventory call for the detected row, in one shell call."""
        st = self._state(project)
        src = target or st.get('target', {}).get('path')
        if not src:
            return 'No target. Run plan() or pass target.'
        src = os.path.expanduser(src)
        if not os.path.exists(src):
            return 'Target not found: %s' % src
        kind = self._detect(src)
        st['type'] = kind
        row = ROUTER.get(kind, ROUTER['generic'])
        st['router'] = row['name']
        cmd = row['first_move'].replace('$T', src).replace('$ROOT', st['dir']).replace('$PY', self._syspython()).replace('$PARSER', self._parser())
        cmd = cmd.replace('NEEDLE', (focus or 'NEEDLE').replace("'", ''))
        return self._run(project, st, cmd, note='recon for %s' % kind, lines=150)

    def run(self, project: str, command: str, note: str='', lines: int=0) -> str:
        """Run one batched probe inside the project dir. Full output is saved; only the head returns."""
        st = self._state(project)
        return self._run(project, st, command, note=note, lines=lines)

    def search(self, project: str, pattern: str, path: str='', limit: int=40) -> str:
        """Search an extracted tree with rg -uu so hidden and ignored files are included."""
        st = self._state(project)
        root = path or os.path.join(st['dir'], 'extracted')
        cmd = "rg -uu -n -m 3 --glob '!node_modules' -- '%s' %s 2>/dev/null | head -%d" % (pattern.replace("'", ''), root, int(limit))
        return self._run(project, st, cmd, note='tree search (rg -uu) for %s' % pattern, lines=int(limit) + 3)

    def slice(self, project: str, file: str, needle: str, window: int=200, limit: int=20) -> str:
        """Slice around a needle instead of reading a whole minified file."""
        st = self._state(project)
        target = file if os.path.isabs(file) else os.path.join(st['dir'], 'work', file)
        if not os.path.exists(target):
            return 'File not found: %s' % target
        cmd = "rg -uu -n -o '.{0,%d}%s.{0,%d}' %s 2>/dev/null | head -%d" % (int(window), needle.replace("'", ''), int(window), target, int(limit))
        if not self._which('rg'):
            cmd = "grep -a -o -- '.{0,%d}%s.{0,%d}' %s 2>/dev/null | head -%d" % (int(window), needle.replace("'"), int(window), target, int(limit))
        return self._run(project, st, cmd, note='slice %s around %s' % (os.path.basename(target), needle), lines=int(limit) + 2)

    def outgrep(self, project: str, pattern: str, seq: int=0, limit: int=40) -> str:
        """Search output already saved by earlier calls instead of re-running an expensive probe."""
        st = self._state(project)
        target = os.path.join(st['dir'], 'out', '%03d.txt' % seq) if seq else os.path.join(st['dir'], 'out')
        if not os.path.exists(target):
            return 'No saved output at %s' % self._rel(st, target)
        cmd = "grep -a -n -m %d -- '%s' %s 2>/dev/null | head -%d" % (int(limit), pattern.replace("'", ''), target, int(limit))
        return self._run(project, st, cmd, note='grep saved output', lines=int(limit) + 1)

    def extract(self, project: str, target: str='', method: str='auto') -> str:
        """Extract a container into its own dir: asar, apktool, jadx, unblob, zip, ar."""
        st = self._state(project)
        src = target or st.get('target', {}).get('path')
        if not src:
            return 'No target. Run plan() or pass target.'
        kind = method if method != 'auto' else self._detect(src)
        dest = os.path.join(st['dir'], 'extracted', kind)
        table = {'asar': "npx -y @electron/asar extract '%s' '%s'", 'apk': "apktool d -no-src -f -o '%s' '%s'", 'zip': "unzip -o -q '%s' -d '%s'", 'dex': "unzip -o -q '%s' -d '%s'", 'jar': "unzip -o -q '%s' -d '%s'", 'firmware': "unblob -e '%s' '%s' 2>&1 | head -20"}
        if kind not in table:
            return 'No extraction method for %s. Route it through route() first.' % kind
        cmd = table[kind] % ((dest, src) if kind == 'firmware' else (src, dest))
        return self._run(project, st, cmd, note='extract %s as %s' % (os.path.basename(src), kind), lines=40)

    def decompile(self, project: str, target: str='', function: str='', mode: str='r2') -> str:
        """Pseudocode for one function via r2, or a full Ghidra headless decompile to disk."""
        st = self._state(project)
        src = target or os.path.join(st['dir'], 'work', os.path.basename(st.get('target', {}).get('path', '')))
        if not os.path.exists(src):
            return 'Target not found in work dir: %s. Run copy() first.' % src
        if mode == 'r2':
            if not function:
                return "Pass function for r2 mode, or mode='ghidra' for the whole binary."
            if not self._which('r2'):
                return "r2 missing. Fallback: objdump -d -M intel '%s' > %s/dis/%s.txt then grep that file." % (src, st['dir'], os.path.basename(src))
            cmd = "r2 -qc 'aaa; pdf @ sym.%s' '%s' 2>/dev/null | head -120" % (function.replace("'", ''), src)
            return self._run(project, st, cmd, note='r2 pseudocode for %s' % function, lines=150)
        if not self._which('analyzeHeadless'):
            return 'analyzeHeadless missing. Fallback: r2 for one function, or install ghidra with install().'
        out = os.path.join(st['dir'], 'dis', 'decompile.txt')
        cmd = "analyzeHeadless '%s' leanrea_proj -import '%s' -scriptPath '%s' -postScript DecompileAll.java -deleteProject 2>&1 | tail -5" % (os.path.join(st['dir'], 'dis'), src, self._scripts())
        return self._run(project, st, cmd, note='ghidra headless decompile', lines=40, env_extra={'LEANREA_OUT': out})

    def parse(self, project: str, fmt: str, target: str='') -> str:
        """Run a stdlib parser for formats with no CLI: scan, dex, axml, apksig, asar, hermes, pe, macho, wasm."""
        st = self._state(project)
        src = target or st.get('target', {}).get('path')
        if not src or not os.path.exists(src):
            return 'No target to parse.'
        cmd = "%s '%s' %s '%s'" % (self._syspython(), self._parser(), fmt, src)
        return self._run(project, st, cmd, note='parser %s' % fmt, lines=120)

    def find(self, project: str, claim: str, status: str='seen', evidence: str='', source: str='') -> str:
        """Record a finding. status is seen, inferred, or unknown. Cite file:offset or a symbol."""
        st = self._state(project)
        entry = {'claim': claim, 'status': status if status in ('seen', 'inferred', 'unknown') else 'seen', 'evidence': evidence, 'source': source or 'static', 'step': st.get('steps', 0), 'iso': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}
        st.setdefault('findings', []).append(entry)
        if entry['status'] == 'unknown':
            st.setdefault('questions', []).append(claim)
        self._log(project, st, 'finding', '%s [%s] %s' % (claim, entry['status'], evidence or 'no evidence'))
        self._save(project, st)
        c = {s: len([f for f in st['findings'] if f['status'] == s]) for s in ('seen', 'inferred', 'unknown')}
        return 'recorded: %s [%s]\nledger: %d seen, %d inferred, %d unknown (step %d)%s' % (claim, entry['status'], c['seen'], c['inferred'], c['unknown'], st['steps'], self._budget(st))

    def answer(self, project: str, question: str='') -> str:
        """Build the answer from the ledger only: seen claims cited, inferred marked, unknown listed."""
        st = self._state(project)
        f = st.get('findings', [])
        groups = {s: [x for x in f if x['status'] == s] for s in ('seen', 'inferred', 'unknown')}
        out = ['answer for %s' % (question or st.get('goal') or '-')]
        for label, key in (('SEEN', 'seen'), ('INFERRED', 'inferred'), ('UNKNOWN', 'unknown')):
            out.append('\n%s (%d):' % (label, len(groups[key])))
            for x in groups[key]:
                out.append('  - %s  %s' % (x['claim'], x['evidence'] or ('no evidence' if key != 'unknown' else '')))
        if not f:
            out.append('\nThe ledger is empty, so there is no answer yet.')
        static = len([x for x in f if x.get('source') == 'static'])
        out.append('\nCoverage: %d of %d claims evidenced. %d static, %d runtime. Static reading is not observed execution.' % (len(groups['seen']), len(f), static, len(f) - static))
        text = '\n'.join(out)
        self._log(project, st, 'answer', question or st.get('goal') or '-', out_chars=len(text))
        self._save(project, st)
        return self._cap(st, text)

    def log(self, project: str, tail: int=15, pattern: str='') -> str:
        """Read the per-project log: every step in order with action, status and detail."""
        st = self._state(project)
        path = os.path.join(st['dir'], 'log.jsonl')
        if not os.path.exists(path):
            return 'No log yet for project %s' % st['project']
        if pattern:
            cmd = "grep -a -n -m %d -- '%s' '%s' | head -%d" % (int(tail), pattern.replace("'", ''), path, int(tail))
            return self._run(project, st, cmd, note='log search', lines=int(tail) + 1)
        with open(path, 'r', encoding='utf-8') as fh:
            lines = [x for x in fh.read().splitlines() if x.strip()]
        rows = []
        for line in lines[-int(tail):]:
            try:
                r = json.loads(line)
                rows.append('%3d %s [%s/%s] %s' % (r['seq'], r['iso'], r['action'], r['status'], r['detail']))
            except Exception:
                rows.append(line)
        text = '\n'.join(rows)
        st['context']['chars'] += len(text)
        self._save(project, st)
        return self._cap(st, text)

    def resume(self, project: str) -> str:
        """Rebuild working context after a crash or context loss, from the log and ledger on disk."""
        st = self._state(project)
        if not os.path.exists(os.path.join(st['dir'], 'state.json')):
            return 'No state for project %s. Start with plan().' % st['project']
        artifacts = []
        for dp, _, fns in os.walk(st['dir']):
            for fn in fns:
                if fn in ('state.json', 'log.jsonl', 'log.md'):
                    continue
                artifacts.append(os.path.relpath(os.path.join(dp, fn), st['dir']))
        out = ['resume project %s' % st['project'], 'goal: %s' % (st.get('goal') or '-'), 'authorization: %s' % st.get('authorization'), 'machine: %s root: %s' % (st.get('machine', {}).get('os', '-'), st.get('machine', {}).get('root', '-')), 'target: %s (%s, %d bytes)' % (st.get('target', {}).get('path', '-'), st.get('type', '-'), st.get('target', {}).get('size', 0)), 'route: %s' % (st.get('router') or '-'), 'steps completed: %d' % st.get('steps', 0), '', 'ledger:', '\n'.join(('  [%s] %s  %s' % (f['status'], f['claim'], f['evidence']) for f in st.get('findings', []))) or '  (empty)', '', 'open questions:', '\n  '.join(st.get('questions') or ['-']), 'next actions:', '\n  '.join(st.get('next') or ['-']), '', 'last steps:', '\n'.join(('  %s' % x for x in self.log(project, tail=8).splitlines())), '', 'artifacts on disk: %s' % (', '.join(artifacts[:40]) or 'none')]
        text = '\n'.join(out)
        self._log(project, st, 'resume', 'context rebuilt from disk', out_chars=len(text))
        self._save(project, st)
        return self._cap(st, text)

    def handoff(self, project: str) -> str:
        """Compact handoff for a fresh context window: established facts, open questions, next actions."""
        st = self._state(project)
        seen = [f for f in st.get('findings', []) if f['status'] == 'seen']
        open_claims = [f for f in st.get('findings', []) if f['status'] == 'unknown']
        out = ['handoff for %s' % st['project'], 'goal: %s' % (st.get('goal') or '-'), 'target: %s (%s)' % (st.get('target', {}).get('path', '-'), st.get('type') or '-'), 'route used: %s' % (st.get('router') or '-'), 'steps %d, context %d of %d' % (st['steps'], st['context'].get('chars', 0), st['context']['ceiling']), '', 'established, do not re-verify:', '\n'.join(('  - %s [%s]' % (f['claim'], f['evidence']) for f in seen)) or '  (nothing yet)', '', 'still open:', '\n'.join(('  - %s' % f['claim'] for f in open_claims)) or '  (none)', '', 'next actions:', '\n  '.join(('  - %s' % a for a in st.get('next', []))) or '  (none)', '', 'files: %s | %s | %s' % (self._rel(st, os.path.join(st['dir'], 'log.md')), self._rel(st, os.path.join(st['dir'], 'state.json')), self._rel(st, os.path.join(st['dir'], 'out')))]
        text = '\n'.join(out)
        self._log(project, st, 'handoff', 'handoff emitted', out_chars=len(text))
        self._save(project, st)
        return text

    def stop(self, project: str, reason: str='') -> str:
        """Close a project with a stated reason so a later context knows where it ended and why."""
        st = self._state(project)
        counts = {s: len([f for f in st.get('findings', []) if f['status'] == s]) for s in ('seen', 'inferred', 'unknown')}
        self._log(project, st, 'stop', reason or 'no reason given', status='closed')
        st['closed'] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        self._save(project, st)
        return 'project %s closed at step %d\nreason: %s\nevidenced %d, inferred %d, unresolved %d' % (st['project'], st['steps'], reason or '-', counts['seen'], counts['inferred'], counts['unknown'])

    def _detect(self, path: str) -> str:
        path = os.path.expanduser(path)
        try:
            with open(path, 'rb') as f:
                head = f.read(64)
        except Exception:
            return 'unreadable'
        if head.startswith(b'MZ') and self._which('file'):
            try:
                out = subprocess.run(['file', '-b', path], capture_output=True, timeout=20)
                desc = out.stdout.decode('utf-8', 'replace').lower()
                if 'mono' in desc or '.net' in desc or 'assembly' in desc:
                    return 'dotnet'
            except Exception:
                pass
        for magic, kind in MAGIC:
            if head.startswith(magic):
                return kind
        try:
            n2 = struct.unpack('<I', head[4:8])[0]
            if 0 < n2 < 20000000:
                with open(path, 'rb') as f:
                    f.seek(8)
                    obj = json.loads(f.read(n2).decode('utf-8', 'replace'))
                if isinstance(obj, dict) and ('files' in obj or 'header' in obj):
                    return 'asar'
        except Exception:
            pass
        low = path.lower()
        for ext, kind in (('.apk', 'apk'), ('.dex', 'dex'), ('.har', 'pcap_har'), ('.plist', 'plist'), ('.pcap', 'pcap_har'), ('.pcapng', 'pcap_har'), ('.asar', 'electron'), ('.wasm', 'wasm'), ('.jar', 'jar'), ('.aar', 'jar'), ('.dll', 'elf_pe'), ('.bin', 'firmware'), ('.img', 'firmware')):
            if low.endswith(ext):
                return kind
        try:
            import zipfile
            if zipfile.is_zipfile(path):
                if 'AndroidManifest.xml' in zipfile.ZipFile(path).namelist():
                    return 'apk'
                return 'zip'
        except Exception:
            pass
        if self._which('file'):
            try:
                out = subprocess.run(['file', '-b', path], capture_output=True, timeout=20)
                desc = out.stdout.decode('utf-8', 'replace').strip().lower()
                if 'mach-o' in desc:
                    return 'macho'
                if 'elf' in desc:
                    return 'elf_pe'
                if 'microsoft' in desc or 'pe32' in desc:
                    return 'elf_pe'
                if '.net' in desc or 'mono' in desc:
                    return 'dotnet'
                if 'firmware' in desc or 'filesystem' in desc or 'compress' in desc:
                    return 'firmware'
                if 'sqlite' in desc:
                    return 'sqlite'
                if 'plist' in desc:
                    return 'plist'
            except Exception:
                pass
        return 'generic'
_builtin_tools = Tools()

def toolchain(self, project: str='') -> str:
    """Report which RE tools resolve, which are missing, and what the fallback is."""
    return _builtin_tools.toolchain(project=project)

def install(self, project: str, tools: str='', target: str='') -> str:
    """Install RE tools. Pass a space or comma separated list. With no list, install what
        the detected router row for the target needs. Reports the command when ALLOW_INSTALL is
        off rather than running it."""
    return _builtin_tools.install(project=project, tools=tools, target=target)

def ensure(self, project: str, target: str='', auto: bool=True) -> str:
    """Check the detected row's tools. Install what is missing if allowed, otherwise give
        the fallback in the same row. A missing tool is never treated as a blocker."""
    return _builtin_tools.ensure(project=project, target=target, auto=auto)

def rules(self, project: str='') -> str:
    """Reload leanrea's operating rules. Cheap way to restore discipline in a fresh context."""
    return _builtin_tools.rules(project=project)

def plan(self, project: str, goal: str='', target: str='', authorization: str='', questions: str='', next_action: str='') -> str:
    """Open or update a project: goal, target, authorization basis, open questions, next actions."""
    return _builtin_tools.plan(project=project, goal=goal, target=target, authorization=authorization, questions=questions, next_action=next_action)

def route(self, target: str, project: str='') -> str:
    """Detect by magic bytes first, then the fastest path, the traps, and the first move."""
    return _builtin_tools.route(target=target, project=project)

def copy(self, project: str, target: str='') -> str:
    """Copy the target into the project work dir so the original is never touched."""
    return _builtin_tools.copy(project=project, target=target)

def recon(self, project: str, target: str='', focus: str='') -> str:
    """Run the single batched inventory call for the detected row, in one shell call."""
    return _builtin_tools.recon(project=project, target=target, focus=focus)

def run(self, project: str, command: str, note: str='', lines: int=0) -> str:
    """Run one batched probe inside the project dir. Full output is saved; only the head returns."""
    return _builtin_tools.run(project=project, command=command, note=note, lines=lines)

def search(self, project: str, pattern: str, path: str='', limit: int=40) -> str:
    """Search an extracted tree with rg -uu so hidden and ignored files are included."""
    return _builtin_tools.search(project=project, pattern=pattern, path=path, limit=limit)

def slice(self, project: str, file: str, needle: str, window: int=200, limit: int=20) -> str:
    """Slice around a needle instead of reading a whole minified file."""
    return _builtin_tools.slice(project=project, file=file, needle=needle, window=window, limit=limit)

def outgrep(self, project: str, pattern: str, seq: int=0, limit: int=40) -> str:
    """Search output already saved by earlier calls instead of re-running an expensive probe."""
    return _builtin_tools.outgrep(project=project, pattern=pattern, seq=seq, limit=limit)

def extract(self, project: str, target: str='', method: str='auto') -> str:
    """Extract a container into its own dir: asar, apktool, jadx, unblob, zip, ar."""
    return _builtin_tools.extract(project=project, target=target, method=method)

def decompile(self, project: str, target: str='', function: str='', mode: str='r2') -> str:
    """Pseudocode for one function via r2, or a full Ghidra headless decompile to disk."""
    return _builtin_tools.decompile(project=project, target=target, function=function, mode=mode)

def parse(self, project: str, fmt: str, target: str='') -> str:
    """Run a stdlib parser for formats with no CLI: scan, dex, axml, apksig, asar, hermes, pe, macho, wasm."""
    return _builtin_tools.parse(project=project, fmt=fmt, target=target)

def find(self, project: str, claim: str, status: str='seen', evidence: str='', source: str='') -> str:
    """Record a finding. status is seen, inferred, or unknown. Cite file:offset or a symbol."""
    return _builtin_tools.find(project=project, claim=claim, status=status, evidence=evidence, source=source)

def answer(self, project: str, question: str='') -> str:
    """Build the answer from the ledger only: seen claims cited, inferred marked, unknown listed."""
    return _builtin_tools.answer(project=project, question=question)

def log(self, project: str, tail: int=15, pattern: str='') -> str:
    """Read the per-project log: every step in order with action, status and detail."""
    return _builtin_tools.log(project=project, tail=tail, pattern=pattern)

def resume(self, project: str) -> str:
    """Rebuild working context after a crash or context loss, from the log and ledger on disk."""
    return _builtin_tools.resume(project=project)

def handoff(self, project: str) -> str:
    """Compact handoff for a fresh context window: established facts, open questions, next actions."""
    return _builtin_tools.handoff(project=project)

def stop(self, project: str, reason: str='') -> str:
    """Close a project with a stated reason so a later context knows where it ended and why."""
    return _builtin_tools.stop(project=project, reason=reason)
