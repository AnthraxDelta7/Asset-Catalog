"""Every user-triggerable function in the app, as one named list.

A command is a stable id, a label, and a default key. Menus, context
menus and panel buttons all trigger the *same* QAction rather than each
wiring up their own copy of a handler, which is what makes a function
bindable at all -- a QPushButton with a clicked signal can't be given a
shortcut, so anything living only on a button was previously unreachable
from the keyboard no matter what the user wanted.

Ids are permanent. They're what a user's saved keybinding refers to, so
renaming one silently drops whatever they had bound to it; the label is
the part that's safe to reword.

Two kinds of shortcut, told apart automatically rather than declared:
a sequence with a modifier (Ctrl+E) is always live, while a bare key
(F, Space) is suppressed whenever a text field has focus. Without that,
typing "fbx" into the search box would fire three commands. See
CommandRegistry.set_text_focus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    default_shortcut: str = ""
    category: str = "General"


# Grouped by category purely for how a future rebinding UI would list
# them; the registry itself treats the list as flat.
COMMANDS: list[Command] = [
    # -- File ---------------------------------------------------------
    Command("file.settings", "Settings...", "Ctrl+,", "File"),
    Command("file.switch_library", "Switch Library...", "Ctrl+Shift+O", "File"),
    Command("file.exit", "Exit", "Ctrl+Q", "File"),
    # -- Edit / filtering ---------------------------------------------
    Command("edit.select_all", "Select All", "Ctrl+A", "Edit"),
    Command("edit.trash", "Move Selected to Trash", "Del", "Edit"),
    Command("edit.focus_search", "Focus Search", "Ctrl+F", "Edit"),
    Command("edit.clear_filters", "Clear All Filters", "Ctrl+Shift+F", "Edit"),
    # -- Asset --------------------------------------------------------
    Command("asset.favorite", "Toggle Favorite", "F", "Asset"),
    Command("asset.add_tag", "Add Tag...", "T", "Asset"),
    Command("asset.show_in_library", "Show in Library Folder", "Ctrl+Shift+E", "Asset"),
    Command("asset.regenerate_thumbnail", "Regenerate Thumbnail", "R", "Asset"),
    Command("asset.convert_gltf", "Convert to glTF (.glb)...", "", "Asset"),
    Command("asset.fix_texture", "Fix Missing Texture...", "", "Asset"),
    Command("asset.revert_conversion", "Revert Conversion", "", "Asset"),
    Command("asset.cleanup_conversion", "Keep Converted, Discard Original", "", "Asset"),
    Command("pack.edit_metadata", "Edit Pack Metadata...", "Ctrl+M", "Asset"),
    # -- Preview ------------------------------------------------------
    Command("preview.open_3d", "View in 3D", "Space", "Preview"),
    Command("preview.render_previews", "Render 3D Previews for Selection", "Ctrl+Shift+R", "Preview"),
    # -- Export -------------------------------------------------------
    Command("export.dialog", "Export Selected to Project...", "Ctrl+E", "Export"),
    Command("export.quick", "Export to Last Project", "E", "Export"),
    Command("export.browse", "Export to Project (Browse)...", "", "Export"),
    # -- Navigation ---------------------------------------------------
    Command("nav.next_asset", "Next Asset", "Right", "Navigation"),
    Command("nav.previous_asset", "Previous Asset", "Left", "Navigation"),
    Command("nav.filter_to_pack", "Filter to This Asset's Pack", "Ctrl+P", "Navigation"),
    # -- Thumbnails ---------------------------------------------------
    Command("thumbs.2d", "Generate 2D Thumbnails (current pack filter)", "", "Thumbnails"),
    Command("thumbs.3d", "Generate 3D Thumbnails via Blender (current pack filter)", "", "Thumbnails"),
    Command("thumbs.audio", "Generate Audio Thumbnails (current pack filter)", "", "Thumbnails"),
    # -- Tools --------------------------------------------------------
    Command("tools.ingest", "Ingest Pack...", "Ctrl+I", "Tools"),
    Command("tools.convert_gltf", "Convert Selected to glTF (.glb)...", "", "Tools"),
    Command("tools.convert_flagged", "Convert All Flagged to glTF (.glb)...", "", "Tools"),
    Command("tools.godot_extract", "Extract Godot Scenes to GLB...", "", "Tools"),
    Command("tools.tag_pack", "Tag Pack...", "", "Tools"),
    Command("tools.cleanup_conversions", "Clean Up Pre-Conversion Assets...", "", "Tools"),
    Command("tools.credits", "Generate Credits Report...", "", "Tools"),
    Command("tools.stats", "Library Statistics...", "", "Tools"),
    Command("tools.trash", "View Trash...", "Ctrl+T", "Tools"),
    Command("tools.library_health", "Check Library Integrity...", "", "Tools"),
    # -- Help ---------------------------------------------------------
    Command("help.check_updates", "Check for Updates...", "", "Help"),
    Command("help.about", "About Asset Catalogue", "F1", "Help"),
]

COMMANDS_BY_ID: dict[str, Command] = {command.id: command for command in COMMANDS}


def is_bare_key(sequence: str) -> bool:
    """Whether this shortcut would collide with typing -- a key with no
    modifier held. These are the ones suppressed while a text field has
    focus. An empty (unbound) sequence is not a bare key.
    """
    if not sequence:
        return False
    key_sequence = QKeySequence(sequence)
    if key_sequence.isEmpty():
        return False
    return key_sequence[0].keyboardModifiers().value == 0


@dataclass
class CommandRegistry:
    """Owns one QAction per command, so a menu item, a context menu entry,
    a toolbar button and a keypress are all the same object -- enable it
    once and every route to it enables together.
    """

    parent: QWidget
    actions: dict[str, QAction] = field(default_factory=dict)
    _shortcuts: dict[str, str] = field(default_factory=dict)
    bound: set = field(default_factory=set)
    _text_focus: bool = False

    def build(self, overrides: dict[str, str] | None = None) -> None:
        """Creates every action. `overrides` is the user's own bindings
        (see settings.shortcuts) layered over the declared defaults, so a
        rebinding UI only ever has to write that dict.
        """
        overrides = overrides or {}
        for command in COMMANDS:
            action = QAction(command.label, self.parent)
            action.setObjectName(command.id)
            sequence = overrides.get(command.id, command.default_shortcut)
            self._shortcuts[command.id] = sequence
            if sequence:
                action.setShortcut(QKeySequence(sequence))
            self.actions[command.id] = action

    def action(self, command_id: str) -> QAction:
        return self.actions[command_id]

    def bind(self, command_id: str, handler) -> QAction:
        action = self.actions[command_id]
        action.triggered.connect(handler)
        self.bound.add(command_id)
        return action

    def unbound_shortcuts(self) -> list[str]:
        """Commands that carry a key but have no handler. Pressing one of
        those does nothing at all, which reads as the app being broken --
        worse than the key simply not existing.
        """
        return sorted(
            command_id
            for command_id, sequence in self._shortcuts.items()
            if sequence and command_id not in self.bound
        )

    def set_enabled(self, command_id: str, enabled: bool) -> None:
        self.actions[command_id].setEnabled(enabled)

    def set_text_focus(self, has_text_focus: bool) -> None:
        """Called as focus moves. While a text field is focused, every
        bare-key shortcut is unbound so the keystroke reaches the field
        instead of firing a command; modifier shortcuts stay live.

        The shortcut is cleared rather than the action disabled, because
        disabling would grey out the matching menu item too -- the
        command is still perfectly valid, it just isn't reachable by a
        naked keypress while you're typing.
        """
        if has_text_focus == self._text_focus:
            return
        self._text_focus = has_text_focus
        for command_id, sequence in self._shortcuts.items():
            if not is_bare_key(sequence):
                continue
            action = self.actions[command_id]
            action.setShortcut(QKeySequence() if has_text_focus else QKeySequence(sequence))

    def shortcut_of(self, command_id: str) -> str:
        return self._shortcuts.get(command_id, "")

    def conflicts(self) -> dict[str, list[str]]:
        """Command ids sharing a shortcut, keyed by the sequence. Two
        actions on one key is silently ambiguous in Qt -- whichever Qt
        happens to reach first wins -- so this is worth surfacing rather
        than leaving to be discovered by a key that sometimes works.
        """
        seen: dict[str, list[str]] = {}
        for command_id, sequence in self._shortcuts.items():
            if sequence:
                seen.setdefault(QKeySequence(sequence).toString(), []).append(command_id)
        return {sequence: ids for sequence, ids in seen.items() if len(ids) > 1}
