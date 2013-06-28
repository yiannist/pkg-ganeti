{-| Small module holding program definitions for data collectors.

-}

{-

Copyright (C) 2012 Google Inc.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301, USA.

-}

module Ganeti.DataCollectors.Program (personalities) where

import Ganeti.Common (PersonalityList)
import Ganeti.DataCollectors.CLI (Options)

import qualified Ganeti.DataCollectors.Drbd as Drbd

-- | Supported binaries.
personalities :: PersonalityList Options
personalities = [ ("drbd",   (Drbd.main, Drbd.options, Drbd.arguments,
                             "gathers and displays DRBD statistics in JSON\
                             \ format"))
                ]
