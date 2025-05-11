#!/bin/python3
"""
    HyperTemplate
    an html templating script.

SUMMARY:
    this script copies site files from docs_path to out_path,
    using pseudo-tags to perform basic replacement operations on the
    source documents. As not to conflict with real html tags and attributes,
    pseudo-tags and attributes used by this script always begin with the
    -- prefix. unlike real html, pseudo-tags and attributes are case-sensitive

USING TEMPLATES:
    templates can be used anywhere in an html document in docs_path, including
    within other templates (but not in the template definition).

    the basic structure is as follows:

    <--template
        --name="template-name"
        $field-name="field-value"
        $field-name1="field-value1"
    >
      <inner-html>
    </--template>

    or the alternate syntax:

    <--template
        --name="template-name"
        --fields="field-name: field-value; field-name1: field-value1"
    >
      <inner-html>
    </--template>

    where:
      template-name is filename of template to insert, .html extension omitted.
      field-name(1) and field-value(1) are names and values of fields defined in
          the template.
      inner-html is any html.

DEFINING TEMPLATES:
    Templates are nearly ordinary html files, and should use the file extension
    .html.

    Within a template, fields can be defined using the --field tag:

    <--field --name="field-name">

    when the template is used, any --field tag will be replaced by the field
    value corresponding to field-name as defined in the --fields pseudo-
    attribute of --template.

    fields do not have to be used like normal html tags. they can be used
    anywhere, including inside of strings or other tags.

    template definitions optionally contain no more than one --inner-html tag.
    
    <--inner-html>

    --inner-thml is a so called void tag that must not include any content.
    when the template is used, the inner-html tag will be replaced with the
    content within the --template tag in the source document.
"""

import re, os, sys
import shutil, subprocess, argparse

class textfmt:
    """Collection of text formatting codes as strings"""

    def tput(args):
        check = subprocess.check_output
        try: return check(
                ["tput"] + args.split(),
                stderr=subprocess.DEVNULL
            ).decode('latin1')
        except: return ""

    bold = tput("bold")
    reset = tput("sgr0")
    blue = tput("setaf 4")
    yellow = tput("setaf 3")
    green = tput("setaf 2")
    red = tput("setaf 1")


class BuildError(Exception):
    """An error with all context included"""

    def __init__(self, doc, msg):
        return super().__init__(f"[ {doc.path} ] {str(msg)}")


class DocumentError(Exception):
    """An error that requires context"""


class File:
    """Base class for Document and Blob
    
    PROPERTIES:
        - path: file path
        - relpath: file path relative to directory specified by user
        - mtime (read-only): last modified time
    """

    def __init__(self, path, relpath):
        self.path = path
        self.relpath = relpath
        self._mtime = None

    @property
    def mtime(self):
        if not self._mtime: self._mtime = os.path.getmtime(self.path)
        return self._mtime

    def __str__(self):
        return str(self.path)

    def __repr__(self):
        return f"<{", ".join([attr+": "+str(getattr(self, attr)) for attr in self.__dict__])}>"


class Document(File):
    """A pseudo-html file

    PROPERTIES:
        (see File)
        - content (read-only): str content of file
    """

    def __init__(self, path, relpath):
        super().__init__(path, relpath)
        self._content = None

    @property
    def content(self):
        if self._content: return self._content
        with open(self.path) as file:
            return file.read()


class Blob(File):
    """Any file that is not a Document"""

    pass
    

class PageBuild:
    """A page to build

    PROPERTIES:
        - src: source Document or Blob
        - replace: Document or Blob to replace, or None if new
        - templates: dict of Templates used in src
        - processed:
            *Only present in files that have been or ought to be processed*
            str of content of the processed file or None if yet to be processed
    """

    def __init__(self, src, out_path, replace, templates):
        """Create PageBuild"""
        
        self.src = src; self.out_path = out_path
        self.replace = replace; self.templates = templates
        if type(self.src) == Document and self.templates:
            self.processed = None

    def __str__(self):
        file = str(self.src.relpath)
        string = f"{file}"
        if self.replace: string += f"{textfmt.blue} -> {textfmt.reset}{str(self.replace)}"
        else: string += f"{textfmt.green} [new]{textfmt.reset}"
        if self.templates: string += f" {textfmt.blue}with:{textfmt.reset} {str(list(self.templates.keys()))}"
        return string

class warn:
    """Stores and prints warnings"""

    stack = []
    context = [""]
    def __new__(cls, msg):
        cls.stack.append(
            textfmt.yellow
            + f"Warning:{" ".join(cls.context + [msg])}"
            + textfmt.reset
        )

    @classmethod
    def add_context(cls, msg):
        cls.context.append(msg)

    @classmethod
    def remove_context(cls):
        cls.context.pop()

    @classmethod
    def print(cls):
        while warn.stack: print(warn.stack.pop())

def index_ignore_quoted(text, string, begin_index=0):
    quote = None;
    for i in range(begin_index, len(text) - len(string) + 1):
        if not quote:
            if (
                (text[i] == '"' or text[i] == "'")
                and not (i > 0 and text[i-1] == '\\')
            ):
                quote = text[i]
            elif text[i:i + len(string)] == string:
                return i
        elif text[i] == quote:
            quote = None
    raise ValueError('not present')


def find_tag(tag_name, text):
    """Find innermost first tag of given name in given html text

    RETURN: tuple of indicies (start, html_start, html_end, end)
             or None if the tag does not appear in the text.
    EXCEPTIONS: DocumentError on invalid tag
    """

    start, html_start, html_end, end = (0, 0, 0, 0)
    while True:
        match = re.search("</?" + tag_name, text[html_start:])
        if (match == None):
            if (html_start == 0): return None
            else: raise DocumentError("opening tag with no matching close")

        new_start = match.span()[0] + html_start
        try: new_html_start = index_ignore_quoted(text, ">", new_start) + 1
        except ValueError: raise DocumentError("missing `>`")

        if text[new_start + 1] == '/':
            if (html_start == 0): raise DocumentError("closing tag with no matching open")
            html_end = new_start
            end = new_html_start
            break;
        else:
            start, html_start = (new_start, new_html_start)
    return (start, html_start, html_end, end)


def parse_attributes(tag):
    """Parse attributes of an (opening) html tag, given as a string

    RETURN: dictionary of attributes as strings
    EXCEPTIONS: DocumentError on invalid tag
    """

    text, n = re.subn(r"(^<\s*[\w-]+\s*)|(\/?>$)", "", tag)
    if (n != 2): raise DocumentError(f"invalid tag `{tag}`")

    ret = {}
    while text != "":
        m = re.match(r'([\$\w-]+)\s*=\s*"([^"]*?)"\s*', text)
        if not m: 
            raise DocumentError(f"invalid tag `{tag}`")
        ret[m[1]] = m[2]
        text = text[m.end():]

    return ret


def process_template(template, fields_provided, inner_html):
    """Insert content into template

    RETURN: string
    EXCEPTIONS: DocumentError
    """

    unused_fields = set(fields_provided)

    def doc_err(msg):
        raise DocumentError(f"template {template.relpath}: {msg}")
    
    def get_field_dict(field_tag):
        ret = parse_attributes(field_tag)
        if "--name" not in ret:
            raise BuildError(template, f"a field tag is missing the attribute `--name`")
        return ret

    def get_field_value(field_tag):
        field = get_field_dict(field_tag)
        field_name = field["--name"]
        field_default_value = field.get("--default")

        if field_name in fields_provided:
            value = fields_provided[field_name]
        elif field_default_value is not None:
            value = field_default_value
        else:
            raise doc_err(f"field `{field_name}` required")

        unused_fields.discard(field_name)
        return value

    class mapping:
        def __init__(self, si, ei, repl):
            self.start = si; self.end = ei; self.repl = repl

    text = template.content

    field_tags = re.finditer(r'<--field(?: +[\w-]+ *= *"[^"\n\r]*?")* *\/?>', text)
    inner_html_tags = re.finditer(r'<--inner-html(?: +[\w-]+ *= *"[^"\n\r]*?")* *\/?>', text)

    subs = [mapping(t.start(), t.end(), get_field_value(t[0])) for t in field_tags]
    subs += [mapping(t.start(), t.end(), inner_html) for t in inner_html_tags]

    for field in unused_fields:
        warn(f"template {template.relpath}: field {field} not used");

    subs.sort(key=lambda r: r.start)

    cursor = 0
    parts = []
    for i in range(0, len(subs)):
        if (cursor > subs[i].start):
            doc_err(f"overlapping tags")
        
        parts += text[cursor:subs[i].start], subs[i].repl
        cursor = subs[i].end

    parts += text[cursor:]
    return "".join(parts)


def process_html(html_content, templates):
    """Apply templates to html_content

    RETURN: string
    EXCEPTIONS: DocumentError
    """

    text = html_content 

    if not (m := find_tag("--template", text)): return text

    start, html_start, html_end, end = m
    tag = text[start:html_start]
    html = text[html_start:html_end]

    attr = parse_attributes(tag)

    if "--name" not in attr:
        raise DocumentError("template name required")
    name = attr["--name"]

    fields_str = attr.get("--fields")

    template = templates[name]

    fields = {}

    if fields_str:
        for field_str in fields_str.split(";"):
            field_list = field_str.split(":");
            if len(field_list) != 2: raise DocumentError(f"couldn't parse field `{field_str}`") 
            fields[field_list[0].strip()] = field_list[1].strip()

    for name, value in attr.items():
        if name.startswith("$"):
            fields[name[1:]] = value;

    repl = process_template(template, fields, html)

    text = text[:start] + repl + text[end:]
    return process_html(text, templates)


def find_files(wd, dir_rel = ""):
    """Recursively create list of all files in wd and subdirs"""

    pages = []
    if not os.path.isdir(dir := os.path.join(wd, dir_rel)): return pages
    for ent_name in os.listdir(dir):
        ent_rel = os.path.join(dir_rel, ent_name)
        ent_path = os.path.join(wd, ent_rel)
        if os.path.isfile(ent_path):
            is_doc = ent_name.endswith(".html") or ent_name.endswith(".htm")
            pages.append((Document if is_doc else Blob)(ent_path, ent_rel))
        else:
            pages += find_files(wd, ent_rel)
    return pages


def template_names(page):
    """Get names of all templates used in page

    EXCEPTIONS: BuildError on failure to parse tag
    NOTES: Doesn't work with multiple templates yet
    """

    if not type(page) == Document: return set()
    names, text = set(), page.content
    while True:
        try: m = find_tag("--template", text)
        except DocumentError as e:
            raise BuildError(page, e)

        if not m: break
        start, html_start, html_end, end = m
        attr = parse_attributes(text[start:html_start])
        try: names.add(attr["--name"])
        except KeyError:
            raise BuildError(page, "template name required")
        text = text[html_start:html_end] # TODO: THIS IS BROKEN
    return names


def template_ident(template):
    """Get identifier used to refer to a template in pages"""

    return re.sub(r'\.html?$', '', template.relpath)


def create_page_builds(pages_path, templates_path, out_path, force_rebuild=False):
    """Get all page builds required

    EXCEPTIONS: BuildError on failure to parse page
    """

    src_pages = find_files(pages_path)
    built_files = find_files(out_path)
    templates_list = find_files(templates_path)
    templates = {template_ident(doc): doc for doc in templates_list}

    def get_templates_used(page):
        ret = {}
        for name in template_names(page):
            if name not in templates:
                raise BuildError(page, f"`{name}` template doesn't exist")
            ret[name] = templates[name]
        return ret

    get_existing = lambda page: next((f for f in built_files if f.relpath == page.relpath), None)
    has_src = lambda page: next((True for f in src_pages if f.relpath == page.relpath), False)

    if (stray_pages := [f.relpath for f in built_files if not has_src(f)]):
        warn("stray files in build directory\n  - " + "\n  - ".join(stray_pages))

    page_builds = [PageBuild(page, out_path, get_existing(page), get_templates_used(page)) for page in src_pages]
    
    def is_required(build):
        if force_rebuild: return True
        if not build.replace: return True
        return max([build.src] + list(build.templates.values()), key = lambda doc: doc.mtime).mtime > build.replace.mtime

    return [b for b in page_builds if is_required(b)]


def process(page_build):
    """Processes page if needed, storing the result in page_build.processed

    EXCEPTIONS: BuildError
    """

    if not hasattr(page_build, "processed"): return

    warn.add_context(f"[ {page_build.src.relpath} ]")
    try:
        page_build.processed = process_html(page_build.src.content, page_build.templates)
    except DocumentError as e:
        raise BuildError(page_build.src, e)
    warn.remove_context()


def save(page_build):
    """Write page to output file"""

    save_path = os.path.join(page_build.out_path, page_build.src.relpath)
    os.makedirs(os.path.split(save_path)[0], exist_ok=True)

    if not page_build.replace:
        assert(not os.path.exists(save_path))
    else: assert(os.path.isfile(save_path))

    if hasattr(page_build, "processed"):
        with open(save_path, "w") as f:
            f.write(page_build.processed)
    else:
        shutil.copyfile(page_build.src.path, save_path)


def eprint(msg):
    """Print msg in red to stderr"""

    print(textfmt.red + msg + textfmt.reset, file=sys.stderr)


def run(docs_path, template_path, out_path, dry_run=False, force_rebuild=False):
    try:
        page_builds = create_page_builds(docs_path, template_path, out_path, force_rebuild)
        for b in page_builds:
            print(b)
            process(b)
    except BuildError as e:
        eprint(f"Error while processing. No files altered.\n  {e}")
        return 1

    if not page_builds:
        print("nothing to do; all files up to date")
        return 0

    if not dry_run:
        try:
            files_saved = 0
            for b in page_builds:
                save(b)
                files_saved += 1
        except Exception as e:
            err = e
        
        if "files_saved" in locals():
            print(f"{textfmt.blue}Saved {files_saved}/{len(page_builds)} files{textfmt.reset}")

        if "err" in locals():
            eprint(f"Error while saving:\n  {err}")
            return 2 

    warn.print()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='run without altering any files')
    parser.add_argument('--force-rebuild', action='store_true', help='ignore timestamps and rebuild all files')
    parser.add_argument('-d', required=True, metavar='DOCS_PATH')
    parser.add_argument('-t', required=True, metavar='TEMPLATES_PATH')
    parser.add_argument('out_path', metavar='OUTPUT_PATH')
    opts = parser.parse_args(sys.argv[1:])

    ret = run(opts.d, opts.t, opts.out_path, opts.dry_run, opts.force_rebuild)
    exit(ret)
