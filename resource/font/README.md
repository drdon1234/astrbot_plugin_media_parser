# 字体资源目录

此目录保留字体许可证，并作为运行时字体的落盘位置。两份 Noto Sans CJK OTF 文件不纳入插件仓库；插件加载和文本图片渲染前会检查文件大小与 SHA256，缺失或校验失败时从 `drdon1234/fonts` 仓库的 `v1.0.0` Release 自动补全。

运行时文件名固定为：

- `NotoSansCJKsc-Regular.otf`
- `NotoSansCJKsc-Bold.otf`

下载使用唯一 `.part` 临时文件，校验通过后原子替换目标文件；失败或取消产生的临时文件会被清理。
