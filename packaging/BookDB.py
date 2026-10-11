"""PyInstaller 打包入口 —— 生成 `BookDB.exe`（P0 产品化）。

用法（在项目根执行）：

    uv pip install -e ".[build]"
    pyinstaller packaging/bookdb.spec --noconfirm

产物：`dist/BookDB/BookDB.exe`（**目录版** —— 启动快、杀软误报少）。

本文件只是薄薄一层入口，真正的启动逻辑在 `bookdb.gui.main`：
它决定「打开哪个 Library」并拉起窗口。
"""

from bookdb.gui.main import main

if __name__ == "__main__":
    main()
