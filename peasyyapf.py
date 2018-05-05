import os
from gi.repository import Gtk
from gi.repository import Geany
from gi.repository import Peasy
try:
    from yapf.yapflib.yapf_api import FormatCode  # reformat a string of code
    from yapf.yapflib.file_resources import GetDefaultStyleForDir
except ImportError:
    print('No YAPF available')
    FormatCode = None

_ = Peasy.gettext


class YAPFPlugin(Peasy.Plugin):
    __gtype_name__ = "peasyyapf"
    item = None
    keys = None

    def do_enable(self):
        geany_data = self.geany_plugin.geany_data
        self.item = Geany.ui_image_menu_item_new(Gtk.STOCK_EXECUTE,
                                                 _("Format Python Code"))
        self.item.connect("activate", self.on_item_click)
        geany_data.main_widgets.tools_menu.append(self.item)
        self.item.show_all()
        self.keys = self.add_key_group("python_code_format", 1)
        self.keys.add_keybinding("python_code_format", _("Python Code Format"),
                                 self.item, 0, 0)
        return True

    def on_item_click(self, item=None):
        if not FormatCode:
            return
        cur_doc = Geany.document_get_current()
        if not cur_doc or cur_doc.file_type.id != Geany.FiletypeID.FILETYPES_PYTHON:
            return
        sci = cur_doc.editor.sci
        contents = sci.get_contents(-1)
        if not contents:
            return
        style = GetDefaultStyleForDir(os.path.dirname(cur_doc.real_path))
        format_text, formatted = FormatCode(contents, style_config=style)
        if formatted:
            sci.set_text(format_text)
            Geany.msgwin_clear_tab(Geany.MessageWindowTabNum.MESSAGE)
            Geany.msgwin_msg_add_string(Geany.MsgColors.BLACK, -1, cur_doc,
                                        "Code Formatted")
            Geany.msgwin_switch_tab(Geany.MessageWindowTabNum.MESSAGE, False)

    def do_disable(self):
        self.item.destroy()
        self.item = None
        self.keys = None
