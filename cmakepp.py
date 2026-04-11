from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum, auto

import re
import textwrap

from typing import Optional
from __future__ import annotations
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
    FATAL = "FATAL_ERROR"

class Project:
    
    _targets = []

    def __init__(self):
        pass

    def generate():
        pass

    def inject_raw_cmake(fp_StringLiteral : str) -> None: 
        pass

    def generate_if(): #idk owo
        pass

    def add_comment_divider(fp_Comment : str): #just to do the thing i usually do w the ######## thing
        pass

    def enforce_8_bit_requirement(): #idk y ud want this but w/e
        pass

    def enforce_16_bit_requirement():
        pass

    def enforce_32_bit_requirement():
        pass

    def enforce_64_bit_requirement():
        pass

    def generate_option(fp_OptionName: str, fp_DefaultValue: bool, fp_Description: str = ""):
        pass

class Target:
    
    name = ""

    _include_dirs = []
    _link_libs = []
    _sources = []

    _source_groups = [] #idk

    def __init__(self):
        pass

    def set_position_independent():
        pass

    def set_target_include_dirs():
        pass

    def set_target_link_libs():
        pass


