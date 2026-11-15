#!/usr/bin/env bash
# Explicit direct network policy for downloads/installations. Pass the command as arguments.
exec env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple HF_ENDPOINT=https://hf-mirror.com "$@"
