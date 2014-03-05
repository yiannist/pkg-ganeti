{-| Utility functions. -}

{-

Copyright (C) 2009, 2010, 2011, 2012, 2013 Google Inc.

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

module Ganeti.Utils
  ( debug
  , debugFn
  , debugXy
  , sepSplit
  , stdDev
  , if'
  , select
  , applyIf
  , commaJoin
  , ensureQuoted
  , tryRead
  , formatTable
  , printTable
  , parseUnit
  , plural
  , niceSort
  , niceSortKey
  , exitIfBad
  , exitErr
  , exitWhen
  , exitUnless
  , logWarningIfBad
  , rStripSpace
  , newUUID
  , getCurrentTime
  , getCurrentTimeUSec
  , clockTimeToString
  , chompPrefix
  , warn
  , wrap
  , trim
  , defaultHead
  , exitIfEmpty
  , splitEithers
  , recombineEithers
  , resolveAddr
  , setOwnerAndGroupFromNames
  ) where

import Data.Char (toUpper, isAlphaNum, isDigit, isSpace)
import Data.Function (on)
import Data.List
import qualified Data.Map as M
import Control.Monad (foldM)

import Debug.Trace
import Network.Socket

import Ganeti.BasicTypes
import qualified Ganeti.ConstantUtils as ConstantUtils
import Ganeti.Logging
import Ganeti.Runtime
import System.IO
import System.Exit
import System.Posix.Files
import System.Time

-- * Debug functions

-- | To be used only for debugging, breaks referential integrity.
debug :: Show a => a -> a
debug x = trace (show x) x

-- | Displays a modified form of the second parameter before returning
-- it.
debugFn :: Show b => (a -> b) -> a -> a
debugFn fn x = debug (fn x) `seq` x

-- | Show the first parameter before returning the second one.
debugXy :: Show a => a -> b -> b
debugXy = seq . debug

-- * Miscellaneous

-- | Apply the function if condition holds, otherwise use default value.
applyIf :: Bool -> (a -> a) -> a -> a
applyIf b f x = if b then f x else x

-- | Comma-join a string list.
commaJoin :: [String] -> String
commaJoin = intercalate ","

-- | Split a list on a separator and return an array.
sepSplit :: Eq a => a -> [a] -> [[a]]
sepSplit sep s
  | null s    = []
  | null xs   = [x]
  | null ys   = [x,[]]
  | otherwise = x:sepSplit sep ys
  where (x, xs) = break (== sep) s
        ys = drop 1 xs

-- | Simple pluralize helper
plural :: Int -> String -> String -> String
plural 1 s _ = s
plural _ _ p = p

-- | Ensure a value is quoted if needed.
ensureQuoted :: String -> String
ensureQuoted v = if not (all (\c -> isAlphaNum c || c == '.') v)
                 then '\'':v ++ "'"
                 else v

-- * Mathematical functions

-- Simple and slow statistical functions, please replace with better
-- versions

-- | Standard deviation function.
stdDev :: [Double] -> Double
stdDev lst =
  -- first, calculate the list length and sum lst in a single step,
  -- for performance reasons
  let (ll', sx) = foldl' (\(rl, rs) e ->
                           let rl' = rl + 1
                               rs' = rs + e
                           in rl' `seq` rs' `seq` (rl', rs')) (0::Int, 0) lst
      ll = fromIntegral ll'::Double
      mv = sx / ll
      av = foldl' (\accu em -> let d = em - mv in accu + d * d) 0.0 lst
  in sqrt (av / ll) -- stddev

-- *  Logical functions

-- Avoid syntactic sugar and enhance readability. These functions are proposed
-- by some for inclusion in the Prelude, and at the moment they are present
-- (with various definitions) in the utility-ht package. Some rationale and
-- discussion is available at <http://www.haskell.org/haskellwiki/If-then-else>

-- | \"if\" as a function, rather than as syntactic sugar.
if' :: Bool -- ^ condition
    -> a    -- ^ \"then\" result
    -> a    -- ^ \"else\" result
    -> a    -- ^ \"then\" or "else" result depending on the condition
if' True x _ = x
if' _    _ y = y

-- * Parsing utility functions

-- | Parse results from readsPrec.
parseChoices :: (Monad m, Read a) => String -> String -> [(a, String)] -> m a
parseChoices _ _ ((v, ""):[]) = return v
parseChoices name s ((_, e):[]) =
    fail $ name ++ ": leftover characters when parsing '"
           ++ s ++ "': '" ++ e ++ "'"
parseChoices name s _ = fail $ name ++ ": cannot parse string '" ++ s ++ "'"

-- | Safe 'read' function returning data encapsulated in a Result.
tryRead :: (Monad m, Read a) => String -> String -> m a
tryRead name s = parseChoices name s $ reads s

-- | Format a table of strings to maintain consistent length.
formatTable :: [[String]] -> [Bool] -> [[String]]
formatTable vals numpos =
    let vtrans = transpose vals  -- transpose, so that we work on rows
                                 -- rather than columns
        mlens = map (maximum . map length) vtrans
        expnd = map (\(flds, isnum, ml) ->
                         map (\val ->
                                  let delta = ml - length val
                                      filler = replicate delta ' '
                                  in if delta > 0
                                     then if isnum
                                          then filler ++ val
                                          else val ++ filler
                                     else val
                             ) flds
                    ) (zip3 vtrans numpos mlens)
   in transpose expnd

-- | Constructs a printable table from given header and rows
printTable :: String -> [String] -> [[String]] -> [Bool] -> String
printTable lp header rows isnum =
  unlines . map ((++) lp . (:) ' ' . unwords) $
  formatTable (header:rows) isnum

-- | Converts a unit (e.g. m or GB) into a scaling factor.
parseUnitValue :: (Monad m) => String -> m Rational
parseUnitValue unit
  -- binary conversions first
  | null unit                     = return 1
  | unit == "m" || upper == "MIB" = return 1
  | unit == "g" || upper == "GIB" = return kbBinary
  | unit == "t" || upper == "TIB" = return $ kbBinary * kbBinary
  -- SI conversions
  | unit == "M" || upper == "MB"  = return mbFactor
  | unit == "G" || upper == "GB"  = return $ mbFactor * kbDecimal
  | unit == "T" || upper == "TB"  = return $ mbFactor * kbDecimal * kbDecimal
  | otherwise = fail $ "Unknown unit '" ++ unit ++ "'"
  where upper = map toUpper unit
        kbBinary = 1024 :: Rational
        kbDecimal = 1000 :: Rational
        decToBin = kbDecimal / kbBinary -- factor for 1K conversion
        mbFactor = decToBin * decToBin -- twice the factor for just 1K

-- | Tries to extract number and scale from the given string.
--
-- Input must be in the format NUMBER+ SPACE* [UNIT]. If no unit is
-- specified, it defaults to MiB. Return value is always an integral
-- value in MiB.
parseUnit :: (Monad m, Integral a, Read a) => String -> m a
parseUnit str =
  -- TODO: enhance this by splitting the unit parsing code out and
  -- accepting floating-point numbers
  case (reads str::[(Int, String)]) of
    [(v, suffix)] ->
      let unit = dropWhile (== ' ') suffix
      in do
        scaling <- parseUnitValue unit
        return $ truncate (fromIntegral v * scaling)
    _ -> fail $ "Can't parse string '" ++ str ++ "'"

-- | Unwraps a 'Result', exiting the program if it is a 'Bad' value,
-- otherwise returning the actual contained value.
exitIfBad :: String -> Result a -> IO a
exitIfBad msg (Bad s) = exitErr (msg ++ ": " ++ s)
exitIfBad _ (Ok v) = return v

-- | Exits immediately with an error message.
exitErr :: String -> IO a
exitErr errmsg = do
  hPutStrLn stderr $ "Error: " ++ errmsg
  exitWith (ExitFailure 1)

-- | Exits with an error message if the given boolean condition if true.
exitWhen :: Bool -> String -> IO ()
exitWhen True msg = exitErr msg
exitWhen False _  = return ()

-- | Exits with an error message /unless/ the given boolean condition
-- if true, the opposite of 'exitWhen'.
exitUnless :: Bool -> String -> IO ()
exitUnless cond = exitWhen (not cond)

-- | Unwraps a 'Result', logging a warning message and then returning a default
-- value if it is a 'Bad' value, otherwise returning the actual contained value.
logWarningIfBad :: String -> a -> Result a -> IO a
logWarningIfBad msg defVal (Bad s) = do
  logWarning $ msg ++ ": " ++ s
  return defVal
logWarningIfBad _ _ (Ok v) = return v

-- | Print a warning, but do not exit.
warn :: String -> IO ()
warn = hPutStrLn stderr . (++) "Warning: "

-- | Helper for 'niceSort'. Computes the key element for a given string.
extractKey :: [Either Integer String]  -- ^ Current (partial) key, reversed
           -> String                   -- ^ Remaining string
           -> ([Either Integer String], String)
extractKey ek [] = (reverse ek, [])
extractKey ek xs@(x:_) =
  let (span_fn, conv_fn) = if isDigit x
                             then (isDigit, Left . read)
                             else (not . isDigit, Right)
      (k, rest) = span span_fn xs
  in extractKey (conv_fn k:ek) rest

{-| Sort a list of strings based on digit and non-digit groupings.

Given a list of names @['a1', 'a10', 'a11', 'a2']@ this function
will sort the list in the logical order @['a1', 'a2', 'a10', 'a11']@.

The sort algorithm breaks each name in groups of either only-digits or
no-digits, and sorts based on each group.

Internally, this is not implemented via regexes (like the Python
version), but via actual splitting of the string in sequences of
either digits or everything else, and converting the digit sequences
in /Left Integer/ and the non-digit ones in /Right String/, at which
point sorting becomes trivial due to the built-in 'Either' ordering;
we only need one extra step of dropping the key at the end.

-}
niceSort :: [String] -> [String]
niceSort = niceSortKey id

-- | Key-version of 'niceSort'. We use 'sortBy' and @compare `on` fst@
-- since we don't want to add an ordering constraint on the /a/ type,
-- hence the need to only compare the first element of the /(key, a)/
-- tuple.
niceSortKey :: (a -> String) -> [a] -> [a]
niceSortKey keyfn =
  map snd . sortBy (compare `on` fst) .
  map (\s -> (fst . extractKey [] $ keyfn s, s))

-- | Strip space characthers (including newline). As this is
-- expensive, should only be run on small strings.
rStripSpace :: String -> String
rStripSpace = reverse . dropWhile isSpace . reverse

-- | Returns a random UUID.
-- This is a Linux-specific method as it uses the /proc filesystem.
newUUID :: IO String
newUUID = do
  contents <- readFile ConstantUtils.randomUuidFile
  return $! rStripSpace $ take 128 contents

-- | Returns the current time as an 'Integer' representing the number
-- of seconds from the Unix epoch.
getCurrentTime :: IO Integer
getCurrentTime = do
  TOD ctime _ <- getClockTime
  return ctime

-- | Returns the current time as an 'Integer' representing the number
-- of microseconds from the Unix epoch (hence the need for 'Integer').
getCurrentTimeUSec :: IO Integer
getCurrentTimeUSec = do
  TOD ctime pico <- getClockTime
  -- pico: 10^-12, micro: 10^-6, so we have to shift seconds left and
  -- picoseconds right
  return $ ctime * 1000000 + pico `div` 1000000

-- | Convert a ClockTime into a (seconds-only) timestamp.
clockTimeToString :: ClockTime -> String
clockTimeToString (TOD t _) = show t

{-| Strip a prefix from a string, allowing the last character of the prefix
(which is assumed to be a separator) to be absent from the string if the string
terminates there.

\>>> chompPrefix \"foo:bar:\" \"a:b:c\"
Nothing

\>>> chompPrefix \"foo:bar:\" \"foo:bar:baz\"
Just \"baz\"

\>>> chompPrefix \"foo:bar:\" \"foo:bar:\"
Just \"\"

\>>> chompPrefix \"foo:bar:\" \"foo:bar\"
Just \"\"

\>>> chompPrefix \"foo:bar:\" \"foo:barbaz\"
Nothing
-}
chompPrefix :: String -> String -> Maybe String
chompPrefix pfx str =
  if pfx `isPrefixOf` str || str == init pfx
    then Just $ drop (length pfx) str
    else Nothing

-- | Breaks a string in lines with length \<= maxWidth.
--
-- NOTE: The split is OK if:
--
-- * It doesn't break a word, i.e. the next line begins with space
--   (@isSpace . head $ rest@) or the current line ends with space
--   (@null revExtra@);
--
-- * It breaks a very big word that doesn't fit anyway (@null revLine@).
wrap :: Int      -- ^ maxWidth
     -> String   -- ^ string that needs wrapping
     -> [String] -- ^ string \"broken\" in lines
wrap maxWidth = filter (not . null) . map trim . wrap0
  where wrap0 :: String -> [String]
        wrap0 text
          | length text <= maxWidth = [text]
          | isSplitOK               = line : wrap0 rest
          | otherwise               = line' : wrap0 rest'
          where (line, rest) = splitAt maxWidth text
                (revExtra, revLine) = break isSpace . reverse $ line
                (line', rest') = (reverse revLine, reverse revExtra ++ rest)
                isSplitOK =
                  null revLine || null revExtra || startsWithSpace rest
                startsWithSpace (x:_) = isSpace x
                startsWithSpace _     = False

-- | Removes surrounding whitespace. Should only be used in small
-- strings.
trim :: String -> String
trim = reverse . dropWhile isSpace . reverse . dropWhile isSpace

-- | A safer head version, with a default value.
defaultHead :: a -> [a] -> a
defaultHead def []    = def
defaultHead _   (x:_) = x

-- | A 'head' version in the I/O monad, for validating parameters
-- without which we cannot continue.
exitIfEmpty :: String -> [a] -> IO a
exitIfEmpty _ (x:_) = return x
exitIfEmpty s []    = exitErr s

-- | Split an 'Either' list into two separate lists (containing the
-- 'Left' and 'Right' elements, plus a \"trail\" list that allows
-- recombination later.
--
-- This is splitter; for recombination, look at 'recombineEithers'.
-- The sum of \"left\" and \"right\" lists should be equal to the
-- original list length, and the trail list should be the same length
-- as well. The entries in the resulting lists are reversed in
-- comparison with the original list.
splitEithers :: [Either a b] -> ([a], [b], [Bool])
splitEithers = foldl' splitter ([], [], [])
  where splitter (l, r, t) e =
          case e of
            Left  v -> (v:l, r, False:t)
            Right v -> (l, v:r, True:t)

-- | Recombines two \"left\" and \"right\" lists using a \"trail\"
-- list into a single 'Either' list.
--
-- This is the counterpart to 'splitEithers'. It does the opposite
-- transformation, and the output list will be the reverse of the
-- input lists. Since 'splitEithers' also reverses the lists, calling
-- these together will result in the original list.
--
-- Mismatches in the structure of the lists (e.g. inconsistent
-- lengths) are represented via 'Bad'; normally this function should
-- not fail, if lists are passed as generated by 'splitEithers'.
recombineEithers :: (Show a, Show b) =>
                    [a] -> [b] -> [Bool] -> Result [Either a b]
recombineEithers lefts rights trail =
  foldM recombiner ([], lefts, rights) trail >>= checker
    where checker (eithers, [], []) = Ok eithers
          checker (_, lefts', rights') =
            Bad $ "Inconsistent results after recombination, l'=" ++
                show lefts' ++ ", r'=" ++ show rights'
          recombiner (es, l:ls, rs) False = Ok (Left l:es,  ls, rs)
          recombiner (es, ls, r:rs) True  = Ok (Right r:es, ls, rs)
          recombiner (_,  ls, rs) t = Bad $ "Inconsistent trail log: l=" ++
                                      show ls ++ ", r=" ++ show rs ++ ",t=" ++
                                      show t

-- | Default hints for the resolver
resolveAddrHints :: Maybe AddrInfo
resolveAddrHints =
  Just defaultHints { addrFlags = [AI_NUMERICHOST, AI_NUMERICSERV] }

-- | Resolves a numeric address.
resolveAddr :: Int -> String -> IO (Result (Family, SockAddr))
resolveAddr port str = do
  resolved <- getAddrInfo resolveAddrHints (Just str) (Just (show port))
  return $ case resolved of
             [] -> Bad "Invalid results from lookup?"
             best:_ -> Ok (addrFamily best, addrAddress best)

-- | Set the owner and the group of a file (given as names, not numeric id).
setOwnerAndGroupFromNames :: FilePath -> GanetiDaemon -> GanetiGroup -> IO ()
setOwnerAndGroupFromNames filename daemon dGroup = do
  -- TODO: it would be nice to rework this (or getEnts) so that runtimeEnts
  -- is read only once per daemon startup, and then cached for further usage.
  runtimeEnts <- getEnts
  ents <- exitIfBad "Can't find required user/groups" runtimeEnts
  -- note: we use directly ! as lookup failures shouldn't happen, due
  -- to the map construction
  let uid = fst ents M.! daemon
  let gid = snd ents M.! dGroup
  setOwnerAndGroup filename uid gid
