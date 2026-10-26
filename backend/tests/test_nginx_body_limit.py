"""P2：nginx client_max_body_size 与 UPLOAD_MAX 对齐，勿再放行 10G。

## 关于路径假设

这个测试要读**仓库根**下的 `nginx/*.conf`（不在 backend/ 里），所以用
`parents[2]`。

但容器镜像里的布局是**平铺**的：`backend/*` 被 COPY 到 `/app`，
没有 `backend/` 这一层，也就没有仓库根的 `nginx/` 目录。在镜像里跑测试时
`parents[2]` 会指到 `/`，`/nginx/default.conf` 不存在 → FileNotFoundError。

所以这里**找不到文件就 skip**，并明确说明原因 —— 而不是让整个测试套件
在一个「本来就不该有 nginx 配置」的环境里红掉（那会把真实失败淹掉）。

这个测试的价值在**开发机/CI 上**（那里有完整仓库），镜像里跳过不影响其意义。
"""

import pytest
from pathlib import Path

# 仓库根 = backend/tests/ 往上两层
ROOT = Path(__file__).resolve().parents[2]

# 本测试真正关心的两份配置
CONFIGS = ("nginx/default.conf", "nginx/standalone.conf")


@pytest.mark.parametrize("name", CONFIGS)
def test_nginx_body_limit_aligned_with_upload(name):
    path = ROOT / name
    if not path.is_file():
        pytest.skip(
            f"{path} 不存在 —— 通常说明当前是「镜像内平铺布局」"
            f"（backend/* 被 COPY 到 /app，没有仓库根）。"
            f"本测试需在完整仓库（开发机 / CI）里跑。"
        )

    text = path.read_text(encoding="utf-8")
    assert "client_max_body_size 10G" not in text, (
        f"{name} 出现了 client_max_body_size 10G —— 这是历史上放行超大上传的写法，"
        f"应保持 512m（对齐 UPLOAD_MAX_SIZE_MB=500 + 余量）"
    )
    assert "client_max_body_size 512m" in text, (
        f"{name} 缺少 client_max_body_size 512m"
    )
