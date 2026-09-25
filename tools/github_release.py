# -*- coding: utf-8 -*-
"""创建 GitHub Release 并上传安装包资产（v2.3.1 起的发布自动化）。

凭据从 Git Credential Manager 读取（git credential fill），仅在内存中使用。
HTTP 走 http.client 连固定字面量 host（api.github.com / uploads.github.com），
避免动态 URL 请求模式；标题/正文取自 installer/build/v2.3.1-release-notes.md。

用法：python tools/github_release.py
"""
import json
import http.client
import subprocess
import sys
from pathlib import Path

REPO = 'wb2951516-collab/fox-converter'
TAG = 'v2.3.1'
API_HOST = 'api.github.com'          # 固定字面量 host
UPLOAD_HOST = 'uploads.github.com'   # 固定字面量 host
ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / 'installer' / 'build' / 'v2.3.1-release-notes.md'
ASSET = ROOT / 'installer' / 'build' / 'FoxConverter_Setup_v2.3.1.exe'


def gh_token():
    """从 Git Credential Manager 读 github.com 凭据（仅内存）"""
    out = subprocess.run(
        ['git', 'credential', 'fill'], input='protocol=https\nhost=github.com\n\n',
        capture_output=True, text=True, check=True).stdout
    tok = user = None
    for line in out.splitlines():
        if line.startswith('password='):
            tok = line.split('=', 1)[1]
        elif line.startswith('username='):
            user = line.split('=', 1)[1]
    if not tok:
        raise SystemExit('GCM 中未找到 github.com 凭据')
    return user, tok


def _request(host, method, path, token, body=None, headers=None, timeout=300):
    """对固定 host 发起一次 HTTPS 请求，返回 (status, 响应字节)"""
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    hdrs = {
        'Authorization': f'Bearer {token}',
        'User-Agent': 'fox-converter-release',
        'Accept': 'application/vnd.github+json',
    }
    hdrs.update(headers or {})
    try:
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def api_json(path, token, method='GET', payload=None):
    """JSON API 调用；404 返回 (404, {})，其余非 2xx 抛错"""
    data = json.dumps(payload).encode() if payload is not None else None
    status, body = _request(API_HOST, method, path, token, body=data,
                            headers={'Content-Type': 'application/json'})
    if status == 404:
        return 404, {}
    if not (200 <= status < 300):
        raise SystemExit(f'API {method} {path} → {status}: {body[:300]!r}')
    return status, json.loads(body.decode())


def main():
    user, token = gh_token()
    print(f'凭据用户: {user}')

    # 权限自检：仓库可见性 + token 身份
    status, repo = api_json(f'/repos/{REPO}', token)
    print(f'仓库自检: {status} private={repo.get("private")} '
          f'permissions.push={repo.get("permissions", {}).get("push")}')
    if status != 200:
        raise SystemExit('token 无法访问该仓库（可能是 fine-grained token 未授权此仓库）')
    _, me = api_json('/user', token)
    print(f'token 身份: {me.get("login")}')

    # 解析发布说明：标题取「标题:」行，正文取「## 正文（Markdown）」之后
    text = NOTES.read_text(encoding='utf-8')
    title = TAG + '：性能重构'
    for line in text.splitlines():
        if line.startswith('标题: '):
            title = line[len('标题: '):].strip()
            break
    marker = '## 正文（Markdown）'
    body = text.split(marker, 1)[1].lstrip('\n') if marker in text else text

    # 已有同 tag release 则复用，否则创建
    status, rel = api_json(f'/repos/{REPO}/releases/tags/{TAG}', token)
    if status == 200:
        print(f'Release 已存在: {rel["html_url"]}，补传资产')
        rel_id = rel['id']
    else:
        status, rel = api_json(f'/repos/{REPO}/releases', token, method='POST', payload={
            'tag_name': TAG, 'name': title, 'body': body,
            'draft': False, 'prerelease': False, 'make_latest': 'true',
        })
        print(f'Release 创建: {status} {rel["html_url"]}')
        rel_id = rel['id']

    # 上传安装包（已有同名资产先删）
    _, assets = api_json(f'/repos/{REPO}/releases/{rel_id}/assets', token)
    for a in assets:
        if a['name'] == ASSET.name:
            _request(API_HOST, 'DELETE', f'/repos/{REPO}/releases/assets/{a["id"]}', token)
            print(f'删除旧资产: {a["name"]}')
    status, _ = _request(
        UPLOAD_HOST, 'POST',
        f'/repos/{REPO}/releases/{rel_id}/assets?name={ASSET.name}',
        token, body=ASSET.read_bytes(),
        headers={'Content-Type': 'application/octet-stream',
                 'Content-Length': str(ASSET.stat().st_size)},
        timeout=600)
    print(f'资产上传: {status}')

    # 校验
    _, rel = api_json(f'/repos/{REPO}/releases/tags/{TAG}', token)
    names = [a['name'] for a in rel.get('assets', [])]
    print(f'最终 Release: {rel["html_url"]}')
    print(f'资产: {names}')
    ok = 200 <= status < 300 and ASSET.name in names
    print('结论:', 'ALL OK' if ok else '资产异常')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
