{-| Generic code to work with jobs, e.g. submit jobs and check their status.

-}

{-

Copyright (C) 2009, 2010, 2011, 2012 Google Inc.

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

module Ganeti.Jobs
  ( submitJobs
  , execJobsWait
  , execJobsWaitOk
  , waitForJobs
  ) where

import Control.Concurrent (threadDelay)
import Data.List

import Ganeti.BasicTypes
import Ganeti.Errors
import qualified Ganeti.Luxi as L
import Ganeti.OpCodes
import Ganeti.Types

-- | Submits a set of jobs and returns their job IDs without waiting for
-- completion.
submitJobs :: [[MetaOpCode]] -> L.Client -> IO (Result [L.JobId])
submitJobs opcodes client = do
  jids <- L.submitManyJobs client opcodes
  return (case jids of
            Bad e    -> Bad $ "Job submission error: " ++ formatError e
            Ok jids' -> Ok jids')

-- | Executes a set of jobs and waits for their completion, returning their
-- status.
execJobsWait :: [[MetaOpCode]]        -- ^ The list of jobs
             -> ([L.JobId] -> IO ())  -- ^ Post-submission callback
             -> L.Client              -- ^ The Luxi client
             -> IO (Result [(L.JobId, JobStatus)])
execJobsWait opcodes callback client = do
  jids <- submitJobs opcodes client
  case jids of
    Bad e -> return $ Bad e
    Ok jids' -> do
      callback jids'
      waitForJobs jids' client

-- | Polls a set of jobs at an increasing interval until all are finished one
-- way or another.
waitForJobs :: [L.JobId] -> L.Client -> IO (Result [(L.JobId, JobStatus)])
waitForJobs jids client = waitForJobs' 500000 15000000
  where
    waitForJobs' delay maxdelay = do
      -- TODO: this should use WaitForJobChange once it's available in Haskell
      -- land, instead of a fixed schedule of sleeping intervals.
      threadDelay delay
      sts <- L.queryJobsStatus client jids
      case sts of
        Bad e -> return . Bad $ "Checking job status: " ++ formatError e
        Ok sts' -> if any (<= JOB_STATUS_RUNNING) sts' then
                     waitForJobs' (min (delay * 2) maxdelay) maxdelay
                   else
                     return . Ok $ zip jids sts'

-- | Execute jobs and return @Ok@ only if all of them succeeded.
execJobsWaitOk :: [[MetaOpCode]] -> L.Client -> IO (Result ())
execJobsWaitOk opcodes client = do
  let nullog = const (return () :: IO ())
      failed = filter ((/=) JOB_STATUS_SUCCESS . snd)
      fmtfail (i, s) = show (fromJobId i) ++ "=>" ++ jobStatusToRaw s
  sts <- execJobsWait opcodes nullog client
  case sts of
    Bad e -> return $ Bad e
    Ok sts' -> return (if null $ failed sts' then
                         Ok ()
                       else
                         Bad ("The following jobs failed: " ++
                              (intercalate ", " . map fmtfail $ failed sts')))
