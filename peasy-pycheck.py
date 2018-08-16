import os
import io
import importlib
import tokenize
import operator
from gi.repository import Gtk
from gi.repository import Geany
from gi.repository import Peasy
from gi.repository import GLib

_ = Peasy.gettext

available_formatters = []
available_linters = []
DEFAULT_FORMATTER = None
DEFAULT_LINTER = None
DEFAULT_LINE_WIDTH = 79


def is_mod_available(modname):
    try:
        importlib.import_module(modname)
    except ImportError:
        return False
    else:
        return True


while True:
    for formatter in ["black", "autopep8", "yapf"]:
        if is_mod_available(formatter):
            available_formatters.append(formatter)
            if not DEFAULT_FORMATTER:
                DEFAULT_FORMATTER = formatter
    for linter in ["flake8", "pylint", "pyflakes", "pycodestyle"]:
        if is_mod_available(linter):
            available_linters.append(linter)
            if not DEFAULT_LINTER:
                DEFAULT_LINTER = linter
    break

if available_formatters:
    print("Formatter: {}".format(DEFAULT_FORMATTER))

    def get_formatter(name=DEFAULT_FORMATTER):
        if name == "black":
            import black

            GetDefaultStyleForDir = None

            def FormatCode(content, style_config=None):
                try:
                    changed_content = black.format_file_contents(
                        content, line_length=style_config["line_width"], fast=False
                    )
                except black.NothingChanged:
                    return "", False
                else:
                    return changed_content, True

            return FormatCode, GetDefaultStyleForDir
        elif name == "yapf":
            from yapf.yapflib.yapf_api import FormatCode  # reformat a string of code
            from yapf.yapflib.file_resources import GetDefaultStyleForDir

            return FormatCode, GetDefaultStyleForDir
        elif name == "autopep8":
            from autopep8 import fix_code

            GetDefaultStyleForDir = None

            def FormatCode(content, style=None):
                return fix_code(content, options={"max_line_length": style["line_width"]})

            return FormatCode, GetDefaultStyleForDir
        return None, None


else:
    get_formatter = None
if available_linters:
    print("Linter: {}".format(DEFAULT_LINTER))

    def get_patched_checker(name=DEFAULT_LINTER):
        def get_show_msg(err_code, msg, line, col):
            return "{}: [{},{}] {}".format(
                "ERROR" if err_code.startswith("E9") else "WARNING", line, col, msg
            )

        def read_file_line(fd):
            try:
                (coding, lines) = tokenize.detect_encoding(fd.readline)
                textfd = io.TextIOWrapper(fd, coding, line_buffering=True)
                return [l.decode(coding) for l in lines] + textfd.readlines()
            except (LookupError, SyntaxError, UnicodeError):
                return fd.readlines()

        if name == "flake8":
            from flake8.api.legacy import get_style_guide
            from flake8.checker import FileChecker, processor

            class PatchedFileChecker(FileChecker):
                def __init__(self, filename, checks, options, file_lines=None):
                    self.file_lines = file_lines
                    super().__init__(filename, checks, options)

                def _make_processor(self):
                    return processor.FileProcessor(
                        self.filename, self.options, lines=self.file_lines
                    )

            sg = get_style_guide()

            def check_and_get_results(filename, file_content=None):
                file_content = bytes(file_content) if file_content else b""
                py_file = io.BytesIO(file_content)
                flake8_mngr = sg._file_checker_manager
                flake8_mngr.options.max_line_length = DEFAULT_LINE_WIDTH
                checks = flake8_mngr.checks.to_dictionary()
                file_chk = PatchedFileChecker(
                    filename, checks, flake8_mngr.options, file_lines=read_file_line(py_file)
                )
                file_chk.run_checks()
                g = sg._application.guide
                for result in file_chk.results:
                    err_code, line, col, msg, code_str = result
                    if g.handle_error(
                        code=err_code,
                        filename=filename,
                        line_number=line,
                        column_number=col,
                        text=msg,
                        physical_line=code_str,
                    ):
                        msg = get_show_msg(err_code, msg, line, col)
                        yield (line, msg)
                return iter(())

            return check_and_get_results
        elif name == "pycodestyle":
            from pycodestyle import Checker

            def check_and_get_results(filename, file_content=None):
                file_content = bytes(file_content) if file_content else b""
                py_file = io.BytesIO(file_content)
                chk = Checker(filename, lines=read_file_line(py_file))
                chk.max_line_length = DEFAULT_LINE_WIDTH
                results = chk.check_all()
                results = chk.report._deferred_print
                if not results:
                    return iter(())
                for result in chk.report._deferred_print:
                    line, col, err_code, msg, smry = result
                    msg = get_show_msg(err_code, msg, line, col)
                    yield (line, msg)

            return check_and_get_results
        elif name == "pyflakes":
            from pyflakes.api import check
            from pyflakes.reporter import Reporter

            class PyFlakeReporter(Reporter):
                def __init__(self):
                    self.errors = []

                def unexpectedError(self, filename, msg):
                    self.errors.append(("E9", 1, 1, msg))

                def syntaxError(self, filename, msg, lineno, offset, text):
                    self.errors.append(("E9", lineno, offset, msg))

                def flake(self, message):
                    self.errors.append(
                        ("", message.lineno, message.col, message.message % message.message_args)
                    )

            def check_and_get_results(filename, file_content=None):
                rprter = PyFlakeReporter()
                chk = check(file_content, filename, reporter=rprter)
                if not chk:
                    return iter(())
                for result in rprter.errors:
                    err_code, line, col, msg = result
                    msg = get_show_msg(err_code, msg, line, col)
                    yield (line, msg)

            return check_and_get_results
        elif name == "pylint":
            from pylint.lint import PyLinter
            from pylint import utils
            from pylint import interfaces
            from astroid import MANAGER, builder
            from pylint import reporters

            bd = builder.AstroidBuilder(MANAGER)

            class PatchedPyLinter(PyLinter):
                def check(self, filename, file_content):
                    # initialize msgs_state now that all messages have been registered into
                    # the store
                    for msg in self.msgs_store.messages:
                        if not msg.may_be_emitted():
                            self._msgs_state[msg.msgid] = False
                    basename = (
                        os.path.splitext(os.path.basename(filename))[0] if filename else "untitled"
                    )
                    walker = utils.PyLintASTWalker(self)
                    self.config.reports = True
                    _checkers = self.prepare_checkers()
                    tokencheckers = [
                        c
                        for c in _checkers
                        if interfaces.implements(c, interfaces.ITokenChecker) and c is not self
                    ]
                    rawcheckers = [
                        c for c in _checkers if interfaces.implements(c, interfaces.IRawChecker)
                    ]
                    # notify global begin
                    for checker in _checkers:
                        checker.open()
                        if interfaces.implements(checker, interfaces.IAstroidChecker):
                            walker.add_checker(checker)
                    self.set_current_module(basename, filename)
                    ast_node = bd.string_build(file_content, filename, basename)
                    self.file_state = utils.FileState(basename)
                    self._ignore_file = False
                    # fix the current file (if the source file was not available or
                    # if it's actually a c extension)
                    self.current_file = ast_node.file  # pylint: disable=maybe-no-member
                    self.check_astroid_module(ast_node, walker, rawcheckers, tokencheckers)
                    # warn about spurious inline messages handling
                    spurious_messages = self.file_state.iter_spurious_suppression_messages(
                        self.msgs_store
                    )
                    for msgid, line, args in spurious_messages:
                        self.add_message(msgid, line, None, args)
                    # notify global end
                    self.stats["statement"] = walker.nbstatements
                    for checker in reversed(_checkers):
                        checker.close()

            def check_and_get_results(filename, file_content=None):
                if not isinstance(file_content, str):
                    file_content = file_content.decode("utf8") if file_content else ""
                if not filename:
                    filename = ""
                linter = PatchedPyLinter()
                linter.load_default_plugins()
                rp = reporters.json.JSONReporter()
                linter.set_reporter(rp)
                linter.check(filename, file_content)
                for msg in rp.messages:
                    yield msg["line"], "{type}: [{line},{column}] ({message-id}) {message}".format(
                        **msg
                    )

            return check_and_get_results


else:
    get_patched_checker = None


def check_python_code(doc, filename, file_content):
    check_and_get_results = get_patched_checker(DEFAULT_LINTER)
    checks = sorted(
        check_and_get_results(filename, file_content.encode("utf8")), key=operator.itemgetter(0)
    )
    error = False
    for line, msg in checks:
        Geany.msgwin_msg_add_string(Geany.MsgColors.RED, line, doc, msg)
        error = True
    return error


class PyCheckPlugin(Peasy.Plugin, Peasy.PluginConfigure):
    __gtype_name__ = "peasypycheck"
    item = None
    keys = None
    handlers = []

    def do_enable(self):
        geany_data = self.geany_plugin.geany_data
        self.item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE, _("Format Python Code"))
        self.item.connect("activate", self.on_item_click)
        geany_data.main_widgets.tools_menu.append(self.item)
        self.item.show_all()
        self.keys = self.add_key_group("python_code_format", 1)
        self.keys.add_keybinding("python_code_format", _("Python Code Format"), self.item, 0, 0)
        self.set_signal_handler()
        # load startup config
        self.keyfile = GLib.KeyFile.new()
        global DEFAULT_FORMATTER
        global DEFAULT_LINTER
        global DEFAULT_LINE_WIDTH
        self.pycheck_config = os.path.join(geany_data.app.configdir, "plugins/pycheck.conf")
        if os.path.isfile(self.pycheck_config):
            self.keyfile.load_from_file(self.pycheck_config, GLib.KeyFileFlags.KEEP_COMMENTS)
            DEFAULT_FORMATTER = self.keyfile.get_string("pycheck", "formatter")
            DEFAULT_LINTER = self.keyfile.get_string("pycheck", "linter")
            DEFAULT_LINE_WIDTH = self.keyfile.get_integer("pycheck", "line-width")
        return True

    def set_signal_handler(self):
        if not DEFAULT_LINTER:
            return
        o = self.geany_plugin.geany_data.object
        signals = (
            "document-reload",
            "document-open",
            "document-activate",
            "document-before-save",
            "document-save",
        )
        for sig in signals:
            self.handlers.append(o.connect(sig, self.on_document_notify))

    def on_document_notify(self, user_data, doc):
        if not DEFAULT_LINTER or doc.file_type.id != Geany.FiletypeID.FILETYPES_PYTHON:
            return False
        filename = doc.real_path or doc.file_name
        sci = doc.editor.sci
        file_content = sci.get_contents(sci.get_length() + 1).strip()
        if not file_content:
            return False
        Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
        error = check_python_code(doc, filename, file_content)
        if error:
            Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.MESSAGE, False)
        return False

    def on_item_click(self, item=None):
        if not get_formatter:
            return
        cur_doc = Geany.document_get_current()
        if not cur_doc or cur_doc.file_type.id != Geany.FiletypeID.FILETYPES_PYTHON:
            return
        sci = cur_doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return
        code_formatter, default_style_dir = get_formatter(DEFAULT_FORMATTER)
        style = None
        if default_style_dir is not None:
            style = default_style_dir(os.path.dirname(cur_doc.real_path))
            project = self.geany_plugin.geany_data.app.project
            if project and not style:
                style = default_style_dir(project.base_path)
            style["COLUMN_LIMIT"] = DEFAULT_LINE_WIDTH
        else:
            style = {"line_width": DEFAULT_LINE_WIDTH}
        format_text, formatted = code_formatter(contents, style_config=style)
        if formatted:
            sci.set_text(format_text)
            cur_doc.save_file(False)
            Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
            Geany.msgwin_msg_add_string(
                Geany.MsgColors.BLACK, -1, cur_doc, "Code formatted and saved."
            )
            Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.MESSAGE, False)

    def do_disable(self):
        self.item.destroy()
        self.item = None
        self.keys = None
        if self.handlers:
            o = self.geany_plugin.geany_data.object
            for h in self.handlers:
                o.disconnect(h)

    def on_configure_response(self, dlg, response_id, user_data):
        if response_id not in (Gtk.ResponseType.APPLY, Gtk.ResponseType.OK):
            return
        global DEFAULT_FORMATTER
        global DEFAULT_LINTER
        global DEFAULT_LINE_WIDTH
        for child in user_data.get_children():
            if isinstance(child, Gtk.Label):
                continue
            try:
                val = child.get_active_text()
            except AttributeError:
                val = child.get_value_as_int()
                DEFAULT_LINE_WIDTH = val
            if val in available_formatters:
                DEFAULT_FORMATTER = val
            elif val in available_linters:
                DEFAULT_LINTER = val
        if os.path.isfile(self.pycheck_config):
            self.keyfile.load_from_file(self.pycheck_config, GLib.KeyFileFlags.KEEP_COMMENTS)
        self.keyfile.set_string("pycheck", "formatter", DEFAULT_FORMATTER)
        self.keyfile.set_string("pycheck", "linter", DEFAULT_LINTER)
        self.keyfile.set_integer("pycheck", "line-width", DEFAULT_LINE_WIDTH)
        self.keyfile.save_to_file(self.pycheck_config)

    def do_configure(self, dialog):
        align = Gtk.Alignment.new(0, 0, 1, 0)
        align.props.left_padding = 12
        vbox = Gtk.VBox(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_border_width(2)
        label = Gtk.Label(_("Formatter"))
        label.set_alignment(0, 0.5)
        entry = Gtk.ComboBoxText()
        for f in available_formatters:
            entry.append_text(f)
        entry.set_active(available_formatters.index(DEFAULT_FORMATTER))
        vbox.add(label)
        vbox.add(entry)
        label = Gtk.Label(_("Linter"))
        label.set_alignment(0, 0.5)
        entry = Gtk.ComboBoxText()
        for l in available_linters:
            entry.append_text(l)
        entry.set_active(available_linters.index(DEFAULT_LINTER))
        vbox.add(label)
        vbox.add(entry)
        label = Gtk.Label(_("Line Width"))
        label.set_alignment(0, 0.5)
        adj = Gtk.Adjustment(DEFAULT_LINE_WIDTH, 79, 150, 1, 0, 0)
        entry = Gtk.SpinButton()
        entry.set_adjustment(adj)
        entry.set_value(DEFAULT_LINE_WIDTH)
        vbox.add(label)
        vbox.add(entry)
        align.add(vbox)
        dialog.connect("response", self.on_configure_response, vbox)
        return align
