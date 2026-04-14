from __future__ import annotations #apparently this gotta be first owo

from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum, auto

import re
import textwrap

from typing import Optional
from collections import defaultdict

class TargetType(Enum):
    EXECUTABLE = auto()
    STATIC_LIB = auto()
    SHARED_LIB = auto()
    OBJECT_LIB = auto()  # useful for game engines

class LinkAccess(Enum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    INTERFACE = "INTERFACE"

class Platform(Enum):
    DARWIN = "APPLE"      # maps to cmake's APPLE variable
    WINDOWS = "WIN32"
    LINUX = "UNIX AND NOT APPLE"

class MessageType(Enum):
    STATUS = "STATUS"
    WARNING = "WARNING"
    FATAL = "FATAL_ERROR"

class Language(Enum):
    CXX = "CXX"
    C = "C"


# ──────────────────────────────────────────────────────────────────────────────
#   CMake Condition System
#   Represents cmake-time conditions (if/elseif blocks).
#   These are distinct from generator expressions (build-time).
# ──────────────────────────────────────────────────────────────────────────────

class CMakeCondition:
    """Base for cmake configure-time condition expressions."""
    def __str__(self) -> str:
        raise NotImplementedError
    
    def __and__(self, fp_Other: CMakeCondition) -> CMakeCondition:
        return AndCondition(self, fp_Other)
    
    def __or__(self, fp_Other: CMakeCondition) -> CMakeCondition:
        return OrCondition(self, fp_Other)
    
    def __invert__(self) -> CMakeCondition:
        return NotCondition(self)


class Var(CMakeCondition):
    """A plain cmake variable used as a boolean condition. e.g. Var("PEACH_WINDOWS")"""
    def __init__(self, fp_Name: str):
        self.pm_Name = fp_Name
    def __str__(self) -> str:
        return self.pm_Name


class NotCondition(CMakeCondition):
    def __init__(self, fp_Inner: CMakeCondition):
        self.pm_Inner = fp_Inner
    def __str__(self) -> str:
        return f"NOT {self.pm_Inner}"


class AndCondition(CMakeCondition):
    def __init__(self, fp_Left: CMakeCondition, fp_Right: CMakeCondition):
        self.pm_Left  = fp_Left
        self.pm_Right = fp_Right
    def __str__(self) -> str:
        return f"{self.pm_Left} AND {self.pm_Right}"


class OrCondition(CMakeCondition):
    def __init__(self, fp_Left: CMakeCondition, fp_Right: CMakeCondition):
        self.pm_Left  = fp_Left
        self.pm_Right = fp_Right
    def __str__(self) -> str:
        return f"{self.pm_Left} OR {self.pm_Right}"


class EqualCondition(CMakeCondition):
    """e.g. EqualCondition("CMAKE_SIZEOF_VOID_P", "8")"""
    def __init__(self, fp_Lhs: str, fp_Rhs: str, fp_Negate: bool = False):
        self.pm_Lhs    = fp_Lhs
        self.pm_Rhs    = fp_Rhs
        self.pm_Negate = fp_Negate
    def __str__(self) -> str:
        op = "NOT EQUAL" if self.pm_Negate else "EQUAL"
        return f"${{{self.pm_Lhs}}} {op} {self.pm_Rhs}"


# ──────────────────────────────────────────────────────────────────────────────
#   Generator Expression System
#   These are build-time expressions evaluated by the cmake generator.
#   Distinct from configure-time CMakeCondition.
# ──────────────────────────────────────────────────────────────────────────────

class GenExpr:
    """
    Wraps a cmake generator expression string.
    Usage:
        GenExpr.config("Debug")                   → $<CONFIG:Debug>
        GenExpr.if_(GenExpr.config("Debug"), "a", "b")  → $<IF:$<CONFIG:Debug>,a,b>
        GenExpr.or_(GenExpr.config("Release"), GenExpr.config("RelWithDebInfo"))
    """
    def __init__(self, fp_Raw: str):
        self.pm_Raw = fp_Raw

    def __str__(self) -> str:
        return self.pm_Raw

    @staticmethod
    def config(fp_Config: str) -> GenExpr:
        return GenExpr(f"$<CONFIG:{fp_Config}>")

    @staticmethod
    def if_(fp_Cond: GenExpr, fp_True: str, fp_False: str) -> GenExpr:
        return GenExpr(f"$<IF:{fp_Cond},{fp_True},{fp_False}>")

    @staticmethod
    def or_(*fp_Conditions: GenExpr) -> GenExpr:
        f_Inner = ",".join(str(c) for c in fp_Conditions)
        return GenExpr(f"$<OR:{f_Inner}>")

    @staticmethod
    def not_(fp_Cond: GenExpr) -> GenExpr:
        return GenExpr(f"$<NOT:{fp_Cond}>")

    @staticmethod
    def strequal(fp_Lhs: str, fp_Rhs: str) -> GenExpr:
        return GenExpr(f"$<STREQUAL:{fp_Lhs},{fp_Rhs}>")

    @staticmethod
    def wrap(fp_Cond: GenExpr) -> GenExpr:
        """Wraps a bool genexpr as a conditional: $<condition:...> form."""
        return GenExpr(f"$<{fp_Cond}")
    

# ──────────────────────────────────────────────────────────────────────────────
#   Source Glob
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SourceGlob:
    """
    Represents a cmake file(GLOB ...) or file(GLOB_RECURSE ...) call.
    fp_ConfigureDepends: adds CONFIGURE_DEPENDS so cmake re-runs when files change.
    """
    pattern:           str
    recurse:           bool = False
    configure_depends: bool = True

    def emit(self, fp_VarName: str) -> str:
        f_Cmd = "GLOB_RECURSE" if self.recurse else "GLOB"
        f_ConfigureDepends = "\n    CONFIGURE_DEPENDS" if self.configure_depends else ""
        return f"file(\n    {f_Cmd}\n    {fp_VarName}{f_ConfigureDepends}\n    \"{self.pattern}\"\n)"


# ──────────────────────────────────────────────────────────────────────────────
#   Imported Static Target (replaces your STATIC_IMPORT macro)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PlatformLibPaths:
    """Release + debug lib paths + include dir for one platform."""
    release_lib: Path
    debug_lib:   Path
    include_dir: Path


class ImportedStaticTarget:
    """
    First-class equivalent of your STATIC_IMPORT macro.
    Emits add_library(NAME STATIC IMPORTED) + set_target_properties(...)
    with per-platform if/endif wrapping.

    Usage:
        f_SDL3 = ImportedStaticTarget("SDL3")
        f_SDL3.add_platform(
            Var("PEACH_WINDOWS"),
            PlatformLibPaths(
                release_lib = Path("third_party/.../SDL3-static.lib"),
                debug_lib   = Path("third_party/.../SDL3-static.lib"),
                include_dir = Path("third_party/.../SDL3/include"),
            )
        )
        project.add_imported_target(f_SDL3)
    """
    def __init__(self, fp_Name: str):
        self.pm_Name:     str                                      = fp_Name
        self.pm_Entries:  list[tuple[CMakeCondition, PlatformLibPaths]] = []
        self.pm_Fallback: Optional[PlatformLibPaths]               = None  # else branch

    def add_platform(self, fp_Condition: CMakeCondition, fp_Paths: PlatformLibPaths) -> ImportedStaticTarget:
        self.pm_Entries.append((fp_Condition, fp_Paths))
        return self  # fluent

    def set_fallback(self, fp_Paths: PlatformLibPaths) -> ImportedStaticTarget:
        self.pm_Fallback = fp_Paths
        return self
    

# ──────────────────────────────────────────────────────────────────────────────
#   Conditional Source/Settings Entry
#   Internal representation for anything that might be platform-gated.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ConditionalEntry:
    """
    Wraps any cmake payload (sources, libs, definitions, etc.)
    with an optional configure-time condition and/or a generator expression condition.
    Either can be None (meaning unconditional).
    """
    payload:      object                    # whatever the owning list stores
    condition:    Optional[CMakeCondition] = None  # configure-time if() block
    gen_expr:     Optional[GenExpr]        = None  # generator expression




# ──────────────────────────────────────────────────────────────────────────────
#   Target
# ──────────────────────────────────────────────────────────────────────────────

class Target:
    """
    Represents a cmake target: executable, static lib, shared lib, or object lib.
    All mutable state is per-instance (no class-level mutable containers).
    """

    def __init__(self, fp_Name: str, fp_Type: TargetType):
        self.pm_Name: str        = fp_Name
        self.pm_Type: TargetType = fp_Type

        # All list fields are instance-owned — no class-level shared state!
        self.pm_Sources:      list[ConditionalEntry] = []  # SourceGlob or str paths
        self.pm_IncludeDirs:  list[ConditionalEntry] = []  # (path, LinkAccess)
        self.pm_LinkLibs:     list[ConditionalEntry] = []  # (name_or_target, LinkAccess)
        self.pm_CompileDefs:  list[ConditionalEntry] = []  # (define_str, LinkAccess)
        self.pm_CompileOpts:  list[ConditionalEntry] = []  # (option_str, LinkAccess)
        self.pm_LinkOpts:     list[ConditionalEntry] = []  # str
        self.pm_SourceGroups: list[tuple]            = []  # (tree, prefix, var_name)
        self.pm_Properties:   list[tuple]            = []  # (key, value)
        self.pm_IsPIC:        bool                   = False  # POSITION_INDEPENDENT_CODE
        self.pm_IsMacOSBundle: bool                  = False

    def set_position_independent(self) -> Target:
        self.pm_IsPIC = True
        return self

    def set_macos_bundle(self) -> Target:
        self.pm_IsMacOSBundle = True
        return self

    def add_sources(
        self,
        fp_Sources: Union[SourceGlob, list[str], str],
        condition: Optional[CMakeCondition] = None,
        gen_expr:  Optional[GenExpr]        = None,
    ) -> Target:
        self.pm_Sources.append(ConditionalEntry(fp_Sources, condition, gen_expr))
        return self

    def add_include_dirs(
        self,
        fp_Dirs:      list[str],
        fp_Access:    LinkAccess                    = LinkAccess.PUBLIC,
        condition:    Optional[CMakeCondition]      = None,
        gen_expr:     Optional[GenExpr]             = None,
    ) -> Target:
        self.pm_IncludeDirs.append(ConditionalEntry((fp_Dirs, fp_Access), condition, gen_expr))
        return self

    def add_link_libs(
        self,
        fp_Libs:    list[str],
        fp_Access:  LinkAccess                 = LinkAccess.PUBLIC,
        condition:  Optional[CMakeCondition]   = None,
        gen_expr:   Optional[GenExpr]          = None,
    ) -> Target:
        self.pm_LinkLibs.append(ConditionalEntry((fp_Libs, fp_Access), condition, gen_expr))
        return self

    def add_compile_defs(
        self,
        fp_Defs:   list[str],
        fp_Access: LinkAccess                  = LinkAccess.PUBLIC,
        condition: Optional[CMakeCondition]    = None,
        gen_expr:  Optional[GenExpr]           = None,
    ) -> Target:
        self.pm_CompileDefs.append(ConditionalEntry((fp_Defs, fp_Access), condition, gen_expr))
        return self

    def add_compile_options(
        self,
        fp_Opts:   list[str],
        fp_Access: LinkAccess                  = LinkAccess.PRIVATE,
        condition: Optional[CMakeCondition]    = None,
        gen_expr:  Optional[GenExpr]           = None,
    ) -> Target:
        self.pm_CompileOpts.append(ConditionalEntry((fp_Opts, fp_Access), condition, gen_expr))
        return self

    def add_link_options(
        self,
        fp_Opts:   list[str],
        fp_Access: LinkAccess                  = LinkAccess.PRIVATE,
        condition: Optional[CMakeCondition]    = None,
        gen_expr:  Optional[GenExpr]           = None,
    ) -> Target:
        self.pm_LinkOpts.append(ConditionalEntry((fp_Opts, fp_Access), condition, gen_expr))
        return self

    def add_source_group(self, fp_Tree: str, fp_Prefix: str, fp_FilesVar: str) -> Target:
        """
        Adds a source_group(TREE ... PREFIX ... FILES ${VAR}) call.
        fp_FilesVar is the cmake variable name holding the file list.
        """
        self.pm_SourceGroups.append((fp_Tree, fp_Prefix, fp_FilesVar))
        return self

    def set_property(self, fp_Key: str, fp_Value: str) -> Target:
        self.pm_Properties.append((fp_Key, fp_Value))
        return self




# ──────────────────────────────────────────────────────────────────────────────
#   Project
# ──────────────────────────────────────────────────────────────────────────────

class Project:
    """
    Top-level cmake project. Owns targets, options, settings, and raw injections.
    Call generate() to emit CMakeLists.txt.
    """

    def __init__(
        self,
        fp_Name:         str,
        fp_Version:      str       = "0.0.1",
        fp_Description:  str       = "",
        fp_Languages:    list[Language] = None,
        fp_CmakeMinVer:  str       = "3.20",
        fp_CxxStandard:  int       = 20,
        fp_CStandard:    int       = 17,
    ):
        self.pm_Name:        str               = fp_Name
        self.pm_Version:     str               = fp_Version
        self.pm_Description: str               = fp_Description
        self.pm_Languages:   list[Language]    = fp_Languages or [Language.CXX, Language.C]
        self.pm_CmakeMinVer: str               = fp_CmakeMinVer
        self.pm_CxxStandard: int               = fp_CxxStandard
        self.pm_CStandard:   int               = fp_CStandard

        # All per-instance — no shared class state
        self.pm_Targets:         list[Target]              = []
        self.pm_ImportedTargets: list[ImportedStaticTarget]= []
        self.pm_Options:         list[tuple]               = []  # (name, default, description)
        self.pm_RawBlocks:       list[tuple]               = []  # (cmake_str, optional_condition)
        self.pm_GlobalProps:     list[tuple]               = []  # (property, value)
        self.pm_GlobalCompileOpts: list[ConditionalEntry]  = []
        self.pm_FindPackages:    list[tuple]               = []  # (name, required, condition)

    # ── Target factory ────────────────────────────────────────────────────────

    def add_target(self, fp_Name: str, fp_Type: TargetType) -> Target:
        f_Target = Target(fp_Name, fp_Type)
        self.pm_Targets.append(f_Target)
        return f_Target

    def add_imported_target(self, fp_Target: ImportedStaticTarget) -> None:
        self.pm_ImportedTargets.append(fp_Target)

    # ── Project-level settings ────────────────────────────────────────────────

    def add_option(
        self,
        fp_Name:         str,
        fp_DefaultValue: bool,
        fp_Description:  str = "",
    ) -> None:
        self.pm_Options.append((fp_Name, fp_DefaultValue, fp_Description))

    def add_global_compile_options(
        self,
        fp_Opts:   list[str],
        condition: Optional[CMakeCondition] = None,
    ) -> None:
        self.pm_GlobalCompileOpts.append(ConditionalEntry(fp_Opts, condition))

    def add_find_package(
        self,
        fp_Name:     str,
        fp_Required: bool                      = True,
        condition:   Optional[CMakeCondition]  = None,
    ) -> None:
        self.pm_FindPackages.append((fp_Name, fp_Required, condition))

    def set_global_property(self, fp_Property: str, fp_Value: str) -> None:
        self.pm_GlobalProps.append((fp_Property, fp_Value))

    # ── Enforcement helpers ───────────────────────────────────────────────────

    def enforce_64_bit(self) -> None:
        """Emits a FATAL_ERROR if not building for 64-bit. Mirrors your cmake block."""
        f_Cmake = textwrap.dedent("""\
            if(NOT ${CMAKE_SIZEOF_VOID_P} EQUAL 8)
                message(FATAL_ERROR "Only 64-bit platforms are supported.")
            endif()
        """)
        self.pm_RawBlocks.append((f_Cmake, None))

    def enforce_build_type(self) -> None:
        """Emits FATAL_ERROR if no build type is set."""
        f_Cmake = textwrap.dedent("""\
            if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
                message(FATAL_ERROR "No build type specified. CMAKE WILL NOW EXIT")
            endif()
        """)
        self.pm_RawBlocks.append((f_Cmake, None))

    # ── Raw escape hatch ──────────────────────────────────────────────────────

    def inject_raw_cmake(
        self,
        fp_Cmake:  str,
        condition: Optional[CMakeCondition] = None,
    ) -> None:
        """
        Escape hatch for cmake that cmakepp doesn't model natively.
        Optionally wrap in an if/endif block.
        Try to avoid over-using this — if you find yourself using it constantly
        for the same pattern, that pattern should become a first-class method.
        """
        self.pm_RawBlocks.append((fp_Cmake, condition))

    def add_comment_divider(self, fp_Comment: str) -> None:
        """Emits the ####...#### comment divider you use throughout peach."""
        f_Line      = "#" * 71
        f_Padded    = f"#{fp_Comment.center(69)}#"
        f_Cmake     = f"\n{f_Line}\n{f_Padded}\n{f_Line}\n"
        self.pm_RawBlocks.append((f_Cmake, None))

    # ── Generation ────────────────────────────────────────────────────────────

    def generate(self, fp_OutputPath: Path) -> None:
        """
        Walks the configured project and emits a CMakeLists.txt to fp_OutputPath.
        Raise an exception rather than silently producing broken cmake.
        """
        f_Lines: list[str] = []
        self._emit(f_Lines)
        fp_OutputPath.write_text("\n".join(f_Lines), encoding="utf-8")

    def to_string(self) -> str:
        """Returns the generated CMakeLists.txt content as a string (useful for tests)."""
        f_Lines: list[str] = []
        self._emit(f_Lines)
        return "\n".join(f_Lines)

    # ── Internal emit ─────────────────────────────────────────────────────────
    # NOTE: _emit and helpers are stubs — flesh these out as you implement generation.
    # The structure below shows the intended emit order to match your real CMakeLists.

    def _emit(self, fp_Lines: list[str]) -> None:
        self._emit_header(fp_Lines)
        self._emit_options(fp_Lines)
        self._emit_cmake_minimum(fp_Lines)
        self._emit_project(fp_Lines)
        self._emit_standards(fp_Lines)
        self._emit_global_props(fp_Lines)
        self._emit_global_compile_opts(fp_Lines)
        self._emit_raw_blocks(fp_Lines)       # enforcement blocks go here via raw
        self._emit_imported_targets(fp_Lines)
        for f_Target in self.pm_Targets:
            self._emit_target(fp_Lines, f_Target)

    def _emit_header(self, fp_Lines: list[str]) -> None:
        fp_Lines.append(f"# Generated by cmakepp — {self.pm_Name} v{self.pm_Version}")
        fp_Lines.append("# DO NOT EDIT BY HAND")
        fp_Lines.append("")

    def _emit_options(self, fp_Lines: list[str]) -> None:
        if not self.pm_Options:
            return
        fp_Lines.append(_divider("Options"))
        for (f_Name, f_Default, f_Desc) in self.pm_Options:
            f_Val = "ON" if f_Default else "OFF"
            fp_Lines.append(f'option({f_Name} "{f_Desc}" {f_Val})')
        fp_Lines.append("")

    def _emit_cmake_minimum(self, fp_Lines: list[str]) -> None:
        fp_Lines.append(f"cmake_minimum_required(VERSION {self.pm_CmakeMinVer})")
        fp_Lines.append("")

    def _emit_project(self, fp_Lines: list[str]) -> None:
        f_Langs = " ".join(lang.value for lang in self.pm_Languages)
        fp_Lines.append(
            f'project({self.pm_Name} VERSION {self.pm_Version} '
            f'DESCRIPTION "{self.pm_Description}" LANGUAGES {f_Langs})'
        )
        fp_Lines.append("")

    def _emit_standards(self, fp_Lines: list[str]) -> None:
        fp_Lines += [
            f"set(CMAKE_CXX_STANDARD {self.pm_CxxStandard})",
            "set(CMAKE_CXX_STANDARD_REQUIRED ON)",
            f"set(CMAKE_C_STANDARD {self.pm_CStandard})",
            "set(CMAKE_C_STANDARD_REQUIRED ON)",
            "",
        ]

    def _emit_global_props(self, fp_Lines: list[str]) -> None:
        for (f_Prop, f_Val) in self.pm_GlobalProps:
            fp_Lines.append(f"set_property(GLOBAL PROPERTY {f_Prop} {f_Val})")
        if self.pm_GlobalProps:
            fp_Lines.append("")

    def _emit_global_compile_opts(self, fp_Lines: list[str]) -> None:
        for f_Entry in self.pm_GlobalCompileOpts:
            f_Opts       = " ".join(f_Entry.payload)
            f_Cmake_line = f"add_compile_options({f_Opts})"
            _emit_with_condition(fp_Lines, f_Cmake_line, f_Entry.condition)
        if self.pm_GlobalCompileOpts:
            fp_Lines.append("")

    def _emit_raw_blocks(self, fp_Lines: list[str]) -> None:
        for (f_Cmake, f_Cond) in self.pm_RawBlocks:
            _emit_with_condition(fp_Lines, f_Cmake, f_Cond)

    def _emit_imported_targets(self, fp_Lines: list[str]) -> None:
        for f_IT in self.pm_ImportedTargets:
            _emit_imported_static(fp_Lines, f_IT)

    def _emit_target(self, fp_Lines: list[str], fp_Target: Target) -> None:
        # TODO: flesh out per-target emission
        # Emit: add_library / add_executable, target_sources, target_include_directories,
        #       target_link_libraries, target_compile_definitions, target_compile_options,
        #       set_target_properties, source_group calls
        pass


# ──────────────────────────────────────────────────────────────────────────────
#   Internal emit helpers (module-level, not on any class)
# ──────────────────────────────────────────────────────────────────────────────

def _divider(fp_Label: str) -> str:
    f_Line   = "#" * 71
    f_Padded = f"#{fp_Label.center(69)}#"
    return f"\n{f_Line}\n{f_Padded}\n{f_Line}\n"


def _emit_with_condition(
    fp_Lines:     list[str],
    fp_Content:   str,
    fp_Condition: Optional[CMakeCondition],
) -> None:
    """Wraps fp_Content in if/endif if a condition is provided."""
    if fp_Condition is not None:
        fp_Lines.append(f"if({fp_Condition})")
        for f_Line in fp_Content.splitlines():
            fp_Lines.append(f"    {f_Line}" if f_Line.strip() else f_Line)
        fp_Lines.append(f"endif({fp_Condition})")
    else:
        fp_Lines.extend(fp_Content.splitlines())


def _emit_imported_static(fp_Lines: list[str], fp_IT: ImportedStaticTarget) -> None:
    """
    Emits the platform-branched IMPORTED static target block.
    Equivalent to calling your STATIC_IMPORT macro with per-platform paths.
    """
    if not fp_IT.pm_Entries:
        return

    f_Name = fp_IT.pm_Name
    fp_Lines.append(f"# ImportedStaticTarget: {f_Name}")

    for f_Idx, (f_Cond, f_Paths) in enumerate(fp_IT.pm_Entries):
        f_Keyword = "if" if f_Idx == 0 else "elseif"
        fp_Lines.append(f"{f_Keyword}({f_Cond})")
        fp_Lines += [
            f"    add_library({f_Name} STATIC IMPORTED)",
            f"    set_target_properties({f_Name} PROPERTIES",
            f"        IMPORTED_LOCATION_RELEASE        \"{f_Paths.release_lib}\"",
            f"        IMPORTED_LOCATION_DEBUG          \"{f_Paths.debug_lib}\"",
            f"        IMPORTED_LOCATION_RELWITHDEBINFO \"{f_Paths.release_lib}\"",
            f"        IMPORTED_LOCATION_MINSIZEREL     \"{f_Paths.release_lib}\"",
            f"        INTERFACE_INCLUDE_DIRECTORIES    \"{f_Paths.include_dir}\"",
            f"    )",
        ]

    if fp_IT.pm_Fallback is not None:
        f_P = fp_IT.pm_Fallback
        fp_Lines.append("else()")
        fp_Lines += [
            f"    add_library({f_Name} STATIC IMPORTED)",
            f"    set_target_properties({f_Name} PROPERTIES",
            f"        IMPORTED_LOCATION_RELEASE        \"{f_P.release_lib}\"",
            f"        IMPORTED_LOCATION_DEBUG          \"{f_P.debug_lib}\"",
            f"        IMPORTED_LOCATION_RELWITHDEBINFO \"{f_P.release_lib}\"",
            f"        IMPORTED_LOCATION_MINSIZEREL     \"{f_P.release_lib}\"",
            f"        INTERFACE_INCLUDE_DIRECTORIES    \"{f_P.include_dir}\"",
            f"    )",
        ]
    fp_Lines.append("endif()")
    fp_Lines.append("")