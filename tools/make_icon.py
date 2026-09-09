# -*- coding: utf-8 -*-
"""生成应用图标 assets/icon.ico（信封 + 箭头元素，蓝底圆角）"""
from PIL import Image, ImageDraw


def rounded(draw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def make(size=256):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 蓝底圆角方块
    rounded(d, [8, 8, size - 8, size - 8], radius=size // 5, fill=(37, 99, 235, 255))
    # 白色信封
    ew, eh = int(size * 0.62), int(size * 0.44)
    ex, ey = (size - ew) // 2, (size - eh) // 2 - int(size * 0.04)
    rounded(d, [ex, ey, ex + ew, ey + eh], radius=size // 22, fill=(255, 255, 255, 255))
    # 信封折线
    d.line([ex, ey, ex + ew // 2, ey + eh // 2, ex + ew, ey],
           fill=(37, 99, 235, 255), width=max(3, size // 42), joint='curve')
    # 绿色下载箭头（右下角）
    cx, cy = int(size * 0.72), int(size * 0.72)
    ar = int(size * 0.17)
    d.ellipse([cx - ar, cy - ar, cx + ar, cy + ar], fill=(22, 163, 74, 255))
    aw = max(4, size // 26)
    d.line([cx, cy - ar // 2, cx, cy + ar // 2], fill='white', width=aw)
    d.line([cx - ar // 3, cy + ar // 5, cx, cy + ar // 2 + aw // 2],
           fill='white', width=aw)
    d.line([cx + ar // 3, cy + ar // 5, cx, cy + ar // 2 + aw // 2],
           fill='white', width=aw)
    return img


if __name__ == '__main__':
    import os
    os.makedirs('assets', exist_ok=True)
    img = make(256)
    img.save('assets/icon.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save('assets/icon.png')
    print('icon -> assets/icon.ico')
