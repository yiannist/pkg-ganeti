{-| Cluster rolling maintenance helper.

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

module Ganeti.HTools.Program.Hroller
  ( main
  , options
  , arguments
  ) where

import Control.Monad
import Data.List
import Data.Ord

import qualified Data.IntMap as IntMap

import qualified Ganeti.HTools.Container as Container
import qualified Ganeti.HTools.Node as Node

import Ganeti.Common
import Ganeti.HTools.CLI
import Ganeti.HTools.ExtLoader
import Ganeti.HTools.Graph
import Ganeti.HTools.Loader
import Ganeti.Utils

-- | Options list and functions.
options :: IO [OptType]
options = do
  luxi <- oLuxiSocket
  return
    [ luxi
    , oRapiMaster
    , oDataFile
    , oIAllocSrc
    , oOfflineNode
    , oVerbose
    , oQuiet
    , oNoHeaders
    , oSaveCluster
    ]

-- | The list of arguments supported by the program.
arguments :: [ArgCompletion]
arguments = []

-- | Gather statistics for the coloring algorithms.
-- Returns a string with a summary on how each algorithm has performed,
-- in order of non-decreasing effectiveness, and whether it tied or lost
-- with the previous one.
getStats :: [(String, ColorVertMap)] -> String
getStats colorings = snd . foldr helper (0,"") $ algBySize colorings
    where algostat (algo, cmap) = algo ++ ": " ++ size cmap ++ grpsizes cmap
          size cmap = show (IntMap.size cmap) ++ " "
          grpsizes cmap =
            "(" ++ commaJoin (map (show.length) (IntMap.elems cmap)) ++ ")"
          algBySize = sortBy (flip (comparing (IntMap.size.snd)))
          helper :: (String, ColorVertMap) -> (Int, String) -> (Int, String)
          helper el (0, _) = ((IntMap.size.snd) el, algostat el)
          helper el (old, str)
            | old == elsize = (elsize, str ++ " TIE " ++ algostat el)
            | otherwise = (elsize, str ++ " LOOSE " ++ algostat el)
              where elsize = (IntMap.size.snd) el

-- | Main function.
main :: Options -> [String] -> IO ()
main opts args = do
  unless (null args) $ exitErr "This program doesn't take any arguments."

  let verbose = optVerbose opts

  -- Load cluster data. The last two arguments, cluster tags and ipolicy, are
  -- currently not used by this tool.
  ini_cdata@(ClusterData _ fixed_nl ilf _ _) <- loadExternalData opts

  nlf <- setNodeStatus opts fixed_nl

  maybeSaveData (optSaveCluster opts) "original" "before hroller run" ini_cdata

  -- TODO: only online nodes!
  -- TODO: filter by node group
  -- TODO: fail if instances are running (with option to warn only)
  -- TODO: identify master node, and put it last

  nodeGraph <- case Node.mkNodeGraph nlf ilf of
                     Nothing -> exitErr "Cannot create node graph"
                     Just g -> return g

  when (verbose > 2) . putStrLn $ "Node Graph: " ++ show nodeGraph

  let colorAlgorithms = [ ("LF", colorLF)
                        , ("Dsatur", colorDsatur)
                        , ("Dcolor", colorDcolor)
                        ]
      colorings = map (\(v,a) -> (v,(colorVertMap.a) nodeGraph)) colorAlgorithms
      smallestColoring =
        (snd . minimumBy (comparing (IntMap.size . snd))) colorings
      idToName = Node.name  . (`Container.find` nlf)
      nodesbycoloring = map (map idToName) $ IntMap.elems smallestColoring

  when (verbose > 1) . putStrLn $ getStats colorings

  unless (optNoHeaders opts) $
         putStrLn "'Node Reboot Groups'"
  mapM_ (putStrLn . commaJoin) nodesbycoloring
