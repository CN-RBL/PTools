# -*- coding: utf-8 -*-

module_err: set = set([])

try:
    import markdown_it
except ImportError:
    module_err.add("markdown-it-py")
try:
    from rich.logging import RichHandler
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
except ImportError:
    module_err.add("rich")
import logging
import os

try:
    from lxml import html as html2
    from lxml import etree
except ImportError:
    module_err.add("lxml")
import re
import gc
from sys import exit
from concurrent.futures import ThreadPoolExecutor, as_completed

__version__: str = "1.0 R-Dev"

gc.enable()

if module_err:
    for me in module_err:
        logging.error(f"Module {me} is not installed!")
    logging.error(
        f"Tip: The next time you download a Python project, remember to check if the dependencies are installed first"
    )
    exit(1)


# 将格式化函数提升到模块级别，以便多线程调用
def pretty_print_html(html_str: str) -> str:
    """格式化HTML，保留DOCTYPE和注释，缩进4空格，pre/code开始标签同行，
    并处理自定义标记%%c:class1,class2%%"""

    # 内部函数：处理文本中的标记，并添加class到所属元素
    def process_text(text, owner):
        # 跳过pre/code内的标记
        if owner is not None and owner.tag in ("pre", "code"):
            return text
        pattern = r"%%c:([^%]+)%%"
        match = re.search(pattern, text)
        if match:
            class_str = match.group(1).strip()
            # 解析逗号分隔的class列表，去除空格
            classes = [c.strip() for c in class_str.split(",") if c.strip()]
            # 移除标记（只移除第一个）
            new_text = re.sub(pattern, "", text, count=1)
            # 为owner添加class
            if owner is not None and classes:
                existing = owner.get("class", "")
                new_classes = " ".join(classes)
                if existing:
                    owner.set("class", f"{existing} {new_classes}")
                else:
                    owner.set("class", new_classes)
            return new_text
        return text

    # 递归遍历元素树，处理text和tail中的标记
    def process_markup(element, skip: bool = False):
        # 处理element的text（如果不跳过）
        if not skip:
            if element.text and "%%" in element.text:
                element.text = process_text(element.text, element)
        # 处理子元素：如果本元素是pre/code，则子元素应跳过
        child_skip = skip or element.tag in ("pre", "code")
        for child in element:
            process_markup(child, skip=child_skip)
        # 处理element的tail（始终处理，但会检查owner是否为pre/code）
        if element.tail and "%%" in element.tail:
            parent = element.getparent()
            if parent is not None:
                element.tail = process_text(element.tail, parent)

    # 1. 提取DOCTYPE及其之前的内容（如注释）
    doctype_match = re.search(r"(<!DOCTYPE[^>]*>)", html_str, re.IGNORECASE)
    if doctype_match:
        doctype = doctype_match.group(1)
        before_doctype = html_str[: doctype_match.start()]  # DOCTYPE前的注释等
        after_doctype = html_str[doctype_match.end():]  # DOCTYPE后的内容
    else:
        doctype = ""
        before_doctype = ""
        after_doctype = html_str

    try:
        # 2. 将剩余部分解析为完整HTML文档（自动补全缺失的html/body）
        root = html2.document_fromstring(after_doctype)

        # 3. 使用4个空格进行层级缩进
        etree.indent(root, space="    ")

        # 4. 调整 <pre><code> 格式：使其开始标签在同一行
        for pre in root.xpath(".//pre"):
            if len(pre) > 0 and pre[0].tag == "code":
                pre.text = None  # 清除pre本身的缩进文本
                pre[0].tail = "\n"  # code后换行

        # 5. 处理自定义标记 %%c:class%%
        process_markup(root)

        # 6. 序列化为字符串（无需pretty_print，缩进已手动添加）
        formatted_root = etree.tostring(root, encoding="unicode", method="html")

        # 7. 拼接：前置注释 + DOCTYPE + 换行 + 格式化后的文档
        return before_doctype + doctype + "\n" + formatted_root

    except Exception as e:
        warn(f"完整文档解析失败，尝试片段模式: {e}")
        # 降级方案：使用fragments_fromstring确保内容不丢失
        try:
            fragments = html2.fragments_fromstring(html_str)
            pretty_parts = []
            for frag in fragments:
                if isinstance(frag, str):
                    pretty_parts.append(frag)
                else:
                    # 对片段内的元素也尝试indent
                    try:
                        etree.indent(frag, space="    ")
                    except:
                        error(f"Format error:\n\tat {frag}")
                    # 调整pre/code格式
                    for pre in frag.xpath(".//pre"):
                        if len(pre) > 0 and pre[0].tag == "code":
                            pre.text = None
                            pre[0].tail = "\n"
                    # 处理标记
                    process_markup(frag)
                    pretty_parts.append(
                        etree.tostring(frag, encoding="unicode", method="html")
                    )
            return "".join(pretty_parts)
        except Exception as e2:
            error(f"格式化失败，保留原始内容: {e2}")
            return html_str


def process_file(
        path: str, output_dir: str, template_content: str | None, format_all: bool
) -> tuple:
    """处理单个Markdown文件的函数，在线程池中执行"""
    try:
        # 每个线程独立创建markdown解析器，避免共享状态
        md = markdown_it.MarkdownIt("gfm-like", {"typographer": True})
        md.enable(["replacements", "smartquotes"])

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        html: str = md.render(content)
        output_path: str = path2.join(
            output_dir, path2.basename(path).replace(".md", ".html")
        )

        if template_content:
            # 使用原始模板内容进行替换，不修改共享变量
            title_els: list = html2.fromstring(html).xpath(".//h1")
            title: str = title_els[0].text_content() if title_els else "Untitled"
            html: str = template_content.replace("%%title%%", title).replace("%%content%%", html)

        # 先写入原始HTML
        with open(output_path, "w+", encoding="utf-8") as f:
            f.write(html)

            if format_all:
                raw_html = f.read()
                pretty_html = pretty_print_html(raw_html)
                f.write(pretty_html)
                info(f"Formatted: {output_path}")
            else:
                info(f"Converted without formatting: {output_path}")

        return True, path, output_path, None
    except Exception as e:
        return False, path, None, str(e)


def main() -> int:
    console: Console = Console()
    panel: Panel = Panel(
        Text("Welcome to the Web Tools application!", justify="center"),
        title="PTools",
        subtitle="Enjoy your using!",
        style="bold green",
    )
    console.print(panel)
    logging.basicConfig(
        level=logging.DEBUG, format="%(message)s", handlers=[RichHandler()]
    )

    # 去点优化
    globals()["debug"] = logging.debug
    globals()["info"] = logging.info
    globals()["warn"] = logging.warning
    globals()["error"] = logging.error
    globals()["path2"] = os.path
    import importlib.metadata
    import platform
    version = importlib.metadata.version

    info("Starting main process.")
    debug(f"Platform: {platform.platform()}")
    debug(f"Python version: {platform.python_version()}")
    debug(f"markdown-it module version: {version('markdown-it-py')}")
    debug(f"rich module version: {version('rich')}")
    debug(f"lxml module version: {version('lxml')}")
    debug(f"nuitka module version: {version('nuitka')}")
    debug(f"black module version: {version('black')}")
    debug(f"PTools module version: {__version__}")
    del importlib, version, platform

    input_paths: set[str] = set(
        console.input(
            "Input your markdown file [bold]path[/bold] " '("|" to split): '
        ).split("|")
    )
    debug(f"Input paths: {input_paths}")
    # 验证输入文件
    vinput_paths: list[str] = []
    for path in input_paths:
        if not path2.exists(path):
            warn(f'File not found: "{path}"')
        elif not path2.isfile(path):
            warn(f"Path is not a file: {path}")
        elif not (path.endswith(".md") or path.endswith(".markdown")):
            warn(f'Path is not a markdown file: "{path}"')
        else:
            vinput_paths.append(path)
    debug(f"Valid paths: {vinput_paths}")
    if not vinput_paths:
        error("No valid input files.")
        return 1

    output_dir: str = console.input("Input your output directory [bold]path[/bold]: ")
    if not path2.exists(output_dir):
        error(f'Output directory not found: "{output_dir}"')
        return 1
    elif not path2.isdir(output_dir):
        error(f'Output path is not a directory: "{output_dir}"')
        return 1
    debug(f'Output directory: "{output_dir}"')

    template: str = console.input(
        "Input your HTML template file [bold]path[/bold] "
        "(optional, press Enter to skip): "
    )
    template_content = None
    if template:
        if not path2.exists(template):
            error(f'Template file not found: "{template}"')
        elif not path2.isfile(template):
            error(f'Template path is not a file: "{template}"')
        elif not (template.endswith(".html") or template.endswith(".htm")):
            error(f'Template path is not a HTML file: "{template}"')
        else:
            debug(f'Template file: "{template}"')
            with open(template, "r", encoding="utf-8") as f:
                template_content = f.read()

    # 全局询问是否格式化所有文件
    format_all: bool = False
    if vinput_paths:
        pretty_global = console.input("Format all output HTML files? (Y/N): ")
        format_all = pretty_global.lower() in ["y", "yes"]
    else:
        error("No Valid path")

    info("Starting markdown to HTML conversion with thread pool.")

    # 使用线程池并发处理文件
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for path in vinput_paths:
            future = executor.submit(
                process_file, path, output_dir, template_content, format_all
            )
            futures.append(future)

        success_count: int = 0
        fail_count: int = 0
        for future in as_completed(futures):
            success, path, out_path, error_ = future.result()
            if success:
                success_count += 1
                info(f"Successfully converted: {path} -> {out_path}")
            else:
                fail_count += 1
                error(f"Failed to convert {path}: {error_}")

    info(
        f"Finished convert process. Success: {success_count}, Failed: {fail_count}, Total: {success_count + fail_count}"
    )
    update_article_list = console.input(
        "Do you require an update to the article list? (Y/N): "
    )
    if not update_article_list.lower() in ["y", "yes"]:
        return 0
    info("Starting article list update process.")
    gc.collect()  # 强制垃圾回收，释放内存
    success_count = 0
    fail_count = 0

    article_dir = console.input(
        "Please enter the path to the article directory (.html): "
    )
    if not path2.exists(article_dir):
        error(f'Article directory not found: "{article_dir}"')
        return 1
    elif not path2.isdir(article_dir):
        error(f'Article path is not a directory: "{article_dir}"')
        return 1

    article_list_path = console.input("Please enter the path to the article list HTML file: ")
    if not path2.exists(article_list_path):
        error(f'Article list file not found: "{article_list_path}"')
        return 1
    elif not path2.isfile(article_list_path):
        error(f'Article list path is not a file: "{article_list_path}"')
        return 1
    elif not (
            article_list_path.endswith(".html") or article_list_path.endswith(".htm")
    ):
        error(f'Article list path is not a HTML file: "{article_list_path}"')
        return 1

    # 收集所有文章信息（标题和路径）
    articles = []  # 列表元素为 (title, path)
    for filename in os.listdir(article_dir):
        try:
            if not (filename.endswith(".html") or filename.endswith(".htm")):
                continue
            article_path = path2.join(article_dir, filename)
            info(f"Processing article: {article_path}")
            with open(article_path, "r", encoding="utf-8") as f:
                article_content = f.read()
            title_els = html2.fromstring(article_content).xpath(".//h1")
            title = title_els[0].text_content() if title_els else "Untitled"
            articles.append((title, article_path))
        except Exception as e:
            error(f"Failed to process article {filename}: {e}")
            fail_count += 1
        else:
            success_count += 1

    if not articles:
        error("No valid articles found to update the list.")
        return 1

    # 按标题排序
    articles.sort(key=lambda x: x[0])

    # 生成卡片列表（每个卡片是一个div）
    cards: set = set([])
    base_dir = path2.absdir(path2.dirname(article_list_path))
    for title, path in articles:
        rel_path = path2.relpath(path, base_dir)
        card = f'<div class="card"><a href="{rel_path}">{title}</a></div>'
        cards.add(card)
    card_html = "\n".join(cards)

    # 读取文章列表文件，解析为HTML树
    try:
        with open(article_list_path, "r", encoding="utf-8") as f:
            list_content = f.read()
        tree = html2.document_fromstring(list_content)
    except Exception as e:
        error(f"Failed to parse article list file: {e}")
        return 1

    # 删除所有 class 包含 "card" 的 div 元素
    for card_div in tree.xpath('//div[contains(@class, "card")]'):
        parent = card_div.getparent()
        if parent is not None:
            parent.remove(card_div)
            debug("Removed an existing card div.")

    # 查找占位符 %%card%% 所在的文本节点
    placeholder_found: bool = False
    for element in tree.iter():
        if element.text and "%%card%%" in element.text:
            # 将文本节点中的占位符替换为生成的卡片HTML（解析为元素后插入）
            before, after = element.text.split("%%card%%", 1)
            element.text = before or None  # 前半部分保留为text
            # 将卡片字符串解析为元素列表
            card_fragments = html2.fragments_fromstring(card_html)
            # 在当前位置插入卡片元素
            pos: int = 0
            for frag in card_fragments:
                if not isinstance(frag, str):
                    element.insert(pos, frag)
                    pos += 1
            # 处理剩余部分
            if after:
                # 如果after非空，作为tail添加到最后一个卡片元素，或创建新文本节点
                if card_fragments:
                    last = card_fragments[-1]
                    if not isinstance(last, str):
                        if last.tail:
                            last.tail = after + last.tail
                        else:
                            last.tail = after
                else:
                    # 如果没有卡片，直接设置element的tail
                    element.tail = after
            placeholder_found = True
            debug("Replaced placeholder %%card%% with cards.")
            break
        if element.tail and "%%card%%" in element.tail:
            # 处理tail中的占位符
            parent = element.getparent()
            if parent is None:
                continue
            before, after = element.tail.split("%%card%%", 1)
            element.tail = before or None
            # 创建卡片元素列表
            card_fragments = html2.fragments_fromstring(card_html)
            # 插入到element之后
            idx = list(parent).index(element)
            for i, frag in enumerate(card_fragments):
                if not isinstance(frag, str):
                    parent.insert(idx + 1 + i, frag)
            # 处理剩余部分
            if after:
                if card_fragments:
                    last = card_fragments[-1]
                    if not isinstance(last, str):
                        if last.tail:
                            last.tail = after + last.tail
                        else:
                            last.tail = after
            placeholder_found = True
            debug("Replaced placeholder %%card%% in tail.")
            break

    if not placeholder_found:
        error('Placeholder "%%card%%" not found in the article list file.')
        return 1

    # 将修改后的树写回文件（使用pretty_print_html格式化）
    try:
        updated_html = etree.tostring(tree, encoding="unicode", method="html")
        # 使用pretty_print_html进行最终格式化（确保缩进统一）
        final_html = pretty_print_html(updated_html)
        with open(article_list_path, "w", encoding="utf-8") as f:
            f.write(final_html)
        success_count = len(articles)
        info(f"Successfully updated {success_count} cards.")
    except Exception as e:
        error(f"Failed to write updated article list: {e}")
        return 1

    info(
        f"Finished update process. Success: {success_count}, Failed: {fail_count}"
    )
    info("All processes completed.")
    return 1 if fail_count else 0


if __name__ == "__main__":
    exit(main())
