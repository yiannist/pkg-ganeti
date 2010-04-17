#
#

# Copyright (C) 2006, 2007 Google Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.

"""Module implementing the Ganeti locking code."""

# pylint: disable-msg=W0212

# W0212 since e.g. LockSet methods use (a lot) the internals of
# SharedLock

import os
import select
import threading
import time
import errno

from ganeti import errors
from ganeti import utils


def ssynchronized(lock, shared=0):
  """Shared Synchronization decorator.

  Calls the function holding the given lock, either in exclusive or shared
  mode. It requires the passed lock to be a SharedLock (or support its
  semantics).

  """
  def wrap(fn):
    def sync_function(*args, **kwargs):
      lock.acquire(shared=shared)
      try:
        return fn(*args, **kwargs)
      finally:
        lock.release()
    return sync_function
  return wrap


class RunningTimeout(object):
  """Class to calculate remaining timeout when doing several operations.

  """
  __slots__ = [
    "_allow_negative",
    "_start_time",
    "_time_fn",
    "_timeout",
    ]

  def __init__(self, timeout, allow_negative, _time_fn=time.time):
    """Initializes this class.

    @type timeout: float
    @param timeout: Timeout duration
    @type allow_negative: bool
    @param allow_negative: Whether to return values below zero
    @param _time_fn: Time function for unittests

    """
    object.__init__(self)

    if timeout is not None and timeout < 0.0:
      raise ValueError("Timeout must not be negative")

    self._timeout = timeout
    self._allow_negative = allow_negative
    self._time_fn = _time_fn

    self._start_time = None

  def Remaining(self):
    """Returns the remaining timeout.

    """
    if self._timeout is None:
      return None

    # Get start time on first calculation
    if self._start_time is None:
      self._start_time = self._time_fn()

    # Calculate remaining time
    remaining_timeout = self._start_time + self._timeout - self._time_fn()

    if not self._allow_negative:
      # Ensure timeout is always >= 0
      return max(0.0, remaining_timeout)

    return remaining_timeout


class _SingleNotifyPipeConditionWaiter(object):
  """Helper class for SingleNotifyPipeCondition

  """
  __slots__ = [
    "_fd",
    "_poller",
    ]

  def __init__(self, poller, fd):
    """Constructor for _SingleNotifyPipeConditionWaiter

    @type poller: select.poll
    @param poller: Poller object
    @type fd: int
    @param fd: File descriptor to wait for

    """
    object.__init__(self)
    self._poller = poller
    self._fd = fd

  def __call__(self, timeout):
    """Wait for something to happen on the pipe.

    @type timeout: float or None
    @param timeout: Timeout for waiting (can be None)

    """
    running_timeout = RunningTimeout(timeout, True)

    while True:
      remaining_time = running_timeout.Remaining()

      if remaining_time is not None:
        if remaining_time < 0.0:
          break

        # Our calculation uses seconds, poll() wants milliseconds
        remaining_time *= 1000

      try:
        result = self._poller.poll(remaining_time)
      except EnvironmentError, err:
        if err.errno != errno.EINTR:
          raise
        result = None

      # Check whether we were notified
      if result and result[0][0] == self._fd:
        break


class _BaseCondition(object):
  """Base class containing common code for conditions.

  Some of this code is taken from python's threading module.

  """
  __slots__ = [
    "_lock",
    "acquire",
    "release",
    ]

  def __init__(self, lock):
    """Constructor for _BaseCondition.

    @type lock: threading.Lock
    @param lock: condition base lock

    """
    object.__init__(self)

    # Recursive locks are not supported
    assert not hasattr(lock, "_acquire_restore")
    assert not hasattr(lock, "_release_save")

    self._lock = lock

    # Export the lock's acquire() and release() methods
    self.acquire = lock.acquire
    self.release = lock.release

  def _is_owned(self):
    """Check whether lock is owned by current thread.

    """
    if self._lock.acquire(0):
      self._lock.release()
      return False

    return True

  def _check_owned(self):
    """Raise an exception if the current thread doesn't own the lock.

    """
    if not self._is_owned():
      raise RuntimeError("cannot work with un-aquired lock")


class SingleNotifyPipeCondition(_BaseCondition):
  """Condition which can only be notified once.

  This condition class uses pipes and poll, internally, to be able to wait for
  notification with a timeout, without resorting to polling. It is almost
  compatible with Python's threading.Condition, with the following differences:
    - notifyAll can only be called once, and no wait can happen after that
    - notify is not supported, only notifyAll

  """

  __slots__ = _BaseCondition.__slots__ + [
    "_poller",
    "_read_fd",
    "_write_fd",
    "_nwaiters",
    "_notified",
    ]

  _waiter_class = _SingleNotifyPipeConditionWaiter

  def __init__(self, lock):
    """Constructor for SingleNotifyPipeCondition

    """
    _BaseCondition.__init__(self, lock)
    self._nwaiters = 0
    self._notified = False
    self._read_fd = None
    self._write_fd = None
    self._poller = None

  def _check_unnotified(self):
    """Throws an exception if already notified.

    """
    if self._notified:
      raise RuntimeError("cannot use already notified condition")

  def _Cleanup(self):
    """Cleanup open file descriptors, if any.

    """
    if self._read_fd is not None:
      os.close(self._read_fd)
      self._read_fd = None

    if self._write_fd is not None:
      os.close(self._write_fd)
      self._write_fd = None
    self._poller = None

  def wait(self, timeout=None):
    """Wait for a notification.

    @type timeout: float or None
    @param timeout: Waiting timeout (can be None)

    """
    self._check_owned()
    self._check_unnotified()

    self._nwaiters += 1
    try:
      if self._poller is None:
        (self._read_fd, self._write_fd) = os.pipe()
        self._poller = select.poll()
        self._poller.register(self._read_fd, select.POLLHUP)

      wait_fn = self._waiter_class(self._poller, self._read_fd)
      self.release()
      try:
        # Wait for notification
        wait_fn(timeout)
      finally:
        # Re-acquire lock
        self.acquire()
    finally:
      self._nwaiters -= 1
      if self._nwaiters == 0:
        self._Cleanup()

  def notifyAll(self): # pylint: disable-msg=C0103
    """Close the writing side of the pipe to notify all waiters.

    """
    self._check_owned()
    self._check_unnotified()
    self._notified = True
    if self._write_fd is not None:
      os.close(self._write_fd)
      self._write_fd = None


class PipeCondition(_BaseCondition):
  """Group-only non-polling condition with counters.

  This condition class uses pipes and poll, internally, to be able to wait for
  notification with a timeout, without resorting to polling. It is almost
  compatible with Python's threading.Condition, but only supports notifyAll and
  non-recursive locks. As an additional features it's able to report whether
  there are any waiting threads.

  """
  __slots__ = _BaseCondition.__slots__ + [
    "_nwaiters",
    "_single_condition",
    ]

  _single_condition_class = SingleNotifyPipeCondition

  def __init__(self, lock):
    """Initializes this class.

    """
    _BaseCondition.__init__(self, lock)
    self._nwaiters = 0
    self._single_condition = self._single_condition_class(self._lock)

  def wait(self, timeout=None):
    """Wait for a notification.

    @type timeout: float or None
    @param timeout: Waiting timeout (can be None)

    """
    self._check_owned()

    # Keep local reference to the pipe. It could be replaced by another thread
    # notifying while we're waiting.
    my_condition = self._single_condition

    assert self._nwaiters >= 0
    self._nwaiters += 1
    try:
      my_condition.wait(timeout)
    finally:
      assert self._nwaiters > 0
      self._nwaiters -= 1

  def notifyAll(self): # pylint: disable-msg=C0103
    """Notify all currently waiting threads.

    """
    self._check_owned()
    self._single_condition.notifyAll()
    self._single_condition = self._single_condition_class(self._lock)

  def has_waiting(self):
    """Returns whether there are active waiters.

    """
    self._check_owned()

    return bool(self._nwaiters)


class _CountingCondition(object):
  """Wrapper for Python's built-in threading.Condition class.

  This wrapper keeps a count of active waiters. We can't access the internal
  "__waiters" attribute of threading.Condition because it's not thread-safe.

  """
  __slots__ = [
    "_cond",
    "_nwaiters",
    ]

  def __init__(self, lock):
    """Initializes this class.

    """
    object.__init__(self)
    self._cond = threading.Condition(lock=lock)
    self._nwaiters = 0

  def notifyAll(self): # pylint: disable-msg=C0103
    """Notifies the condition.

    """
    return self._cond.notifyAll()

  def wait(self, timeout=None):
    """Waits for the condition to be notified.

    @type timeout: float or None
    @param timeout: Waiting timeout (can be None)

    """
    assert self._nwaiters >= 0

    self._nwaiters += 1
    try:
      return self._cond.wait(timeout=timeout)
    finally:
      self._nwaiters -= 1

  def has_waiting(self):
    """Returns whether there are active waiters.

    """
    return bool(self._nwaiters)


class SharedLock(object):
  """Implements a shared lock.

  Multiple threads can acquire the lock in a shared way, calling
  acquire_shared().  In order to acquire the lock in an exclusive way threads
  can call acquire_exclusive().

  The lock prevents starvation but does not guarantee that threads will acquire
  the shared lock in the order they queued for it, just that they will
  eventually do so.

  """
  __slots__ = [
    "__active_shr_c",
    "__inactive_shr_c",
    "__deleted",
    "__exc",
    "__lock",
    "__pending",
    "__shr",
    ]

  __condition_class = PipeCondition

  def __init__(self):
    """Construct a new SharedLock.

    """
    object.__init__(self)

    # Internal lock
    self.__lock = threading.Lock()

    # Queue containing waiting acquires
    self.__pending = []

    # Active and inactive conditions for shared locks
    self.__active_shr_c = self.__condition_class(self.__lock)
    self.__inactive_shr_c = self.__condition_class(self.__lock)

    # Current lock holders
    self.__shr = set()
    self.__exc = None

    # is this lock in the deleted state?
    self.__deleted = False

  def __check_deleted(self):
    """Raises an exception if the lock has been deleted.

    """
    if self.__deleted:
      raise errors.LockError("Deleted lock")

  def __is_sharer(self):
    """Is the current thread sharing the lock at this time?

    """
    return threading.currentThread() in self.__shr

  def __is_exclusive(self):
    """Is the current thread holding the lock exclusively at this time?

    """
    return threading.currentThread() == self.__exc

  def __is_owned(self, shared=-1):
    """Is the current thread somehow owning the lock at this time?

    This is a private version of the function, which presumes you're holding
    the internal lock.

    """
    if shared < 0:
      return self.__is_sharer() or self.__is_exclusive()
    elif shared:
      return self.__is_sharer()
    else:
      return self.__is_exclusive()

  def _is_owned(self, shared=-1):
    """Is the current thread somehow owning the lock at this time?

    @param shared:
        - < 0: check for any type of ownership (default)
        - 0: check for exclusive ownership
        - > 0: check for shared ownership

    """
    self.__lock.acquire()
    try:
      return self.__is_owned(shared=shared)
    finally:
      self.__lock.release()

  def _count_pending(self):
    """Returns the number of pending acquires.

    @rtype: int

    """
    self.__lock.acquire()
    try:
      return len(self.__pending)
    finally:
      self.__lock.release()

  def __do_acquire(self, shared):
    """Actually acquire the lock.

    """
    if shared:
      self.__shr.add(threading.currentThread())
    else:
      self.__exc = threading.currentThread()

  def __can_acquire(self, shared):
    """Determine whether lock can be acquired.

    """
    if shared:
      return self.__exc is None
    else:
      return len(self.__shr) == 0 and self.__exc is None

  def __is_on_top(self, cond):
    """Checks whether the passed condition is on top of the queue.

    The caller must make sure the queue isn't empty.

    """
    return self.__pending[0] == cond

  def __acquire_unlocked(self, shared, timeout):
    """Acquire a shared lock.

    @param shared: whether to acquire in shared mode; by default an
        exclusive lock will be acquired
    @param timeout: maximum waiting time before giving up

    """
    self.__check_deleted()

    # We cannot acquire the lock if we already have it
    assert not self.__is_owned(), "double acquire() on a non-recursive lock"

    # Check whether someone else holds the lock or there are pending acquires.
    if not self.__pending and self.__can_acquire(shared):
      # Apparently not, can acquire lock directly.
      self.__do_acquire(shared)
      return True

    if shared:
      wait_condition = self.__active_shr_c

      # Check if we're not yet in the queue
      if wait_condition not in self.__pending:
        self.__pending.append(wait_condition)
    else:
      wait_condition = self.__condition_class(self.__lock)
      # Always add to queue
      self.__pending.append(wait_condition)

    try:
      # Wait until we become the topmost acquire in the queue or the timeout
      # expires.
      while not (self.__is_on_top(wait_condition) and
                 self.__can_acquire(shared)):
        # Wait for notification
        wait_condition.wait(timeout)
        self.__check_deleted()

        # A lot of code assumes blocking acquires always succeed. Loop
        # internally for that case.
        if timeout is not None:
          break

      if self.__is_on_top(wait_condition) and self.__can_acquire(shared):
        self.__do_acquire(shared)
        return True
    finally:
      # Remove condition from queue if there are no more waiters
      if not wait_condition.has_waiting() and not self.__deleted:
        self.__pending.remove(wait_condition)

    return False

  def acquire(self, shared=0, timeout=None, test_notify=None):
    """Acquire a shared lock.

    @type shared: int
    @param shared: whether to acquire in shared mode; by default an
        exclusive lock will be acquired
    @type timeout: float
    @param timeout: maximum waiting time before giving up
    @type test_notify: callable or None
    @param test_notify: Special callback function for unittesting

    """
    self.__lock.acquire()
    try:
      # We already got the lock, notify now
      if __debug__ and callable(test_notify):
        test_notify()

      return self.__acquire_unlocked(shared, timeout)
    finally:
      self.__lock.release()

  def release(self):
    """Release a Shared Lock.

    You must have acquired the lock, either in shared or in exclusive mode,
    before calling this function.

    """
    self.__lock.acquire()
    try:
      assert self.__is_exclusive() or self.__is_sharer(), \
        "Cannot release non-owned lock"

      # Autodetect release type
      if self.__is_exclusive():
        self.__exc = None
      else:
        self.__shr.remove(threading.currentThread())

      # Notify topmost condition in queue
      if self.__pending:
        first_condition = self.__pending[0]
        first_condition.notifyAll()

        if first_condition == self.__active_shr_c:
          self.__active_shr_c = self.__inactive_shr_c
          self.__inactive_shr_c = first_condition

    finally:
      self.__lock.release()

  def delete(self, timeout=None):
    """Delete a Shared Lock.

    This operation will declare the lock for removal. First the lock will be
    acquired in exclusive mode if you don't already own it, then the lock
    will be put in a state where any future and pending acquire() fail.

    @type timeout: float
    @param timeout: maximum waiting time before giving up

    """
    self.__lock.acquire()
    try:
      assert not self.__is_sharer(), "Cannot delete() a lock while sharing it"

      self.__check_deleted()

      # The caller is allowed to hold the lock exclusively already.
      acquired = self.__is_exclusive()

      if not acquired:
        acquired = self.__acquire_unlocked(0, timeout)

        assert self.__is_exclusive() and not self.__is_sharer(), \
          "Lock wasn't acquired in exclusive mode"

      if acquired:
        self.__deleted = True
        self.__exc = None

        # Notify all acquires. They'll throw an error.
        while self.__pending:
          self.__pending.pop().notifyAll()

      return acquired
    finally:
      self.__lock.release()


# Whenever we want to acquire a full LockSet we pass None as the value
# to acquire.  Hide this behind this nicely named constant.
ALL_SET = None


class _AcquireTimeout(Exception):
  """Internal exception to abort an acquire on a timeout.

  """


class LockSet:
  """Implements a set of locks.

  This abstraction implements a set of shared locks for the same resource type,
  distinguished by name. The user can lock a subset of the resources and the
  LockSet will take care of acquiring the locks always in the same order, thus
  preventing deadlock.

  All the locks needed in the same set must be acquired together, though.

  """
  def __init__(self, members=None):
    """Constructs a new LockSet.

    @param members: initial members of the set

    """
    # Used internally to guarantee coherency.
    self.__lock = SharedLock()

    # The lockdict indexes the relationship name -> lock
    # The order-of-locking is implied by the alphabetical order of names
    self.__lockdict = {}

    if members is not None:
      for name in members:
        self.__lockdict[name] = SharedLock()

    # The owner dict contains the set of locks each thread owns. For
    # performance each thread can access its own key without a global lock on
    # this structure. It is paramount though that *no* other type of access is
    # done to this structure (eg. no looping over its keys). *_owner helper
    # function are defined to guarantee access is correct, but in general never
    # do anything different than __owners[threading.currentThread()], or there
    # will be trouble.
    self.__owners = {}

  def _is_owned(self):
    """Is the current thread a current level owner?"""
    return threading.currentThread() in self.__owners

  def _add_owned(self, name=None):
    """Note the current thread owns the given lock"""
    if name is None:
      if not self._is_owned():
        self.__owners[threading.currentThread()] = set()
    else:
      if self._is_owned():
        self.__owners[threading.currentThread()].add(name)
      else:
        self.__owners[threading.currentThread()] = set([name])

  def _del_owned(self, name=None):
    """Note the current thread owns the given lock"""

    assert not (name is None and self.__lock._is_owned()), \
           "Cannot hold internal lock when deleting owner status"

    if name is not None:
      self.__owners[threading.currentThread()].remove(name)

    # Only remove the key if we don't hold the set-lock as well
    if (not self.__lock._is_owned() and
        not self.__owners[threading.currentThread()]):
      del self.__owners[threading.currentThread()]

  def _list_owned(self):
    """Get the set of resource names owned by the current thread"""
    if self._is_owned():
      return self.__owners[threading.currentThread()].copy()
    else:
      return set()

  def _release_and_delete_owned(self):
    """Release and delete all resources owned by the current thread"""
    for lname in self._list_owned():
      lock = self.__lockdict[lname]
      if lock._is_owned():
        lock.release()
      self._del_owned(name=lname)

  def __names(self):
    """Return the current set of names.

    Only call this function while holding __lock and don't iterate on the
    result after releasing the lock.

    """
    return self.__lockdict.keys()

  def _names(self):
    """Return a copy of the current set of elements.

    Used only for debugging purposes.

    """
    # If we don't already own the set-level lock acquired
    # we'll get it and note we need to release it later.
    release_lock = False
    if not self.__lock._is_owned():
      release_lock = True
      self.__lock.acquire(shared=1)
    try:
      result = self.__names()
    finally:
      if release_lock:
        self.__lock.release()
    return set(result)

  def acquire(self, names, timeout=None, shared=0, test_notify=None):
    """Acquire a set of resource locks.

    @param names: the names of the locks which shall be acquired
        (special lock names, or instance/node names)
    @param shared: whether to acquire in shared mode; by default an
        exclusive lock will be acquired
    @type timeout: float or None
    @param timeout: Maximum time to acquire all locks
    @type test_notify: callable or None
    @param test_notify: Special callback function for unittesting

    @return: Set of all locks successfully acquired or None in case of timeout

    @raise errors.LockError: when any lock we try to acquire has
        been deleted before we succeed. In this case none of the
        locks requested will be acquired.

    """
    assert timeout is None or timeout >= 0.0

    # Check we don't already own locks at this level
    assert not self._is_owned(), "Cannot acquire locks in the same set twice"

    # We need to keep track of how long we spent waiting for a lock. The
    # timeout passed to this function is over all lock acquires.
    running_timeout = RunningTimeout(timeout, False)

    try:
      if names is not None:
        # Support passing in a single resource to acquire rather than many
        if isinstance(names, basestring):
          names = [names]

        return self.__acquire_inner(names, False, shared,
                                    running_timeout.Remaining, test_notify)

      else:
        # If no names are given acquire the whole set by not letting new names
        # being added before we release, and getting the current list of names.
        # Some of them may then be deleted later, but we'll cope with this.
        #
        # We'd like to acquire this lock in a shared way, as it's nice if
        # everybody else can use the instances at the same time. If are
        # acquiring them exclusively though they won't be able to do this
        # anyway, though, so we'll get the list lock exclusively as well in
        # order to be able to do add() on the set while owning it.
        if not self.__lock.acquire(shared=shared,
                                   timeout=running_timeout.Remaining()):
          raise _AcquireTimeout()
        try:
          # note we own the set-lock
          self._add_owned()

          return self.__acquire_inner(self.__names(), True, shared,
                                      running_timeout.Remaining, test_notify)
        except:
          # We shouldn't have problems adding the lock to the owners list, but
          # if we did we'll try to release this lock and re-raise exception.
          # Of course something is going to be really wrong, after this.
          self.__lock.release()
          self._del_owned()
          raise

    except _AcquireTimeout:
      return None

  def __acquire_inner(self, names, want_all, shared, timeout_fn, test_notify):
    """Inner logic for acquiring a number of locks.

    @param names: Names of the locks to be acquired
    @param want_all: Whether all locks in the set should be acquired
    @param shared: Whether to acquire in shared mode
    @param timeout_fn: Function returning remaining timeout
    @param test_notify: Special callback function for unittesting

    """
    acquire_list = []

    # First we look the locks up on __lockdict. We have no way of being sure
    # they will still be there after, but this makes it a lot faster should
    # just one of them be the already wrong. Using a sorted sequence to prevent
    # deadlocks.
    for lname in sorted(utils.UniqueSequence(names)):
      try:
        lock = self.__lockdict[lname] # raises KeyError if lock is not there
      except KeyError:
        if want_all:
          # We are acquiring all the set, it doesn't matter if this particular
          # element is not there anymore.
          continue

        raise errors.LockError("Non-existing lock in set (%s)" % lname)

      acquire_list.append((lname, lock))

    # This will hold the locknames we effectively acquired.
    acquired = set()

    try:
      # Now acquire_list contains a sorted list of resources and locks we
      # want.  In order to get them we loop on this (private) list and
      # acquire() them.  We gave no real guarantee they will still exist till
      # this is done but .acquire() itself is safe and will alert us if the
      # lock gets deleted.
      for (lname, lock) in acquire_list:
        if __debug__ and callable(test_notify):
          test_notify_fn = lambda: test_notify(lname)
        else:
          test_notify_fn = None

        timeout = timeout_fn()

        try:
          # raises LockError if the lock was deleted
          acq_success = lock.acquire(shared=shared, timeout=timeout,
                                     test_notify=test_notify_fn)
        except errors.LockError:
          if want_all:
            # We are acquiring all the set, it doesn't matter if this
            # particular element is not there anymore.
            continue

          raise errors.LockError("Non-existing lock in set (%s)" % lname)

        if not acq_success:
          # Couldn't get lock or timeout occurred
          if timeout is None:
            # This shouldn't happen as SharedLock.acquire(timeout=None) is
            # blocking.
            raise errors.LockError("Failed to get lock %s" % lname)

          raise _AcquireTimeout()

        try:
          # now the lock cannot be deleted, we have it!
          self._add_owned(name=lname)
          acquired.add(lname)

        except:
          # We shouldn't have problems adding the lock to the owners list, but
          # if we did we'll try to release this lock and re-raise exception.
          # Of course something is going to be really wrong after this.
          if lock._is_owned():
            lock.release()
          raise

    except:
      # Release all owned locks
      self._release_and_delete_owned()
      raise

    return acquired

  def release(self, names=None):
    """Release a set of resource locks, at the same level.

    You must have acquired the locks, either in shared or in exclusive mode,
    before releasing them.

    @param names: the names of the locks which shall be released
        (defaults to all the locks acquired at that level).

    """
    assert self._is_owned(), "release() on lock set while not owner"

    # Support passing in a single resource to release rather than many
    if isinstance(names, basestring):
      names = [names]

    if names is None:
      names = self._list_owned()
    else:
      names = set(names)
      assert self._list_owned().issuperset(names), (
               "release() on unheld resources %s" %
               names.difference(self._list_owned()))

    # First of all let's release the "all elements" lock, if set.
    # After this 'add' can work again
    if self.__lock._is_owned():
      self.__lock.release()
      self._del_owned()

    for lockname in names:
      # If we are sure the lock doesn't leave __lockdict without being
      # exclusively held we can do this...
      self.__lockdict[lockname].release()
      self._del_owned(name=lockname)

  def add(self, names, acquired=0, shared=0):
    """Add a new set of elements to the set

    @param names: names of the new elements to add
    @param acquired: pre-acquire the new resource?
    @param shared: is the pre-acquisition shared?

    """
    # Check we don't already own locks at this level
    assert not self._is_owned() or self.__lock._is_owned(shared=0), \
      "Cannot add locks if the set is only partially owned, or shared"

    # Support passing in a single resource to add rather than many
    if isinstance(names, basestring):
      names = [names]

    # If we don't already own the set-level lock acquired in an exclusive way
    # we'll get it and note we need to release it later.
    release_lock = False
    if not self.__lock._is_owned():
      release_lock = True
      self.__lock.acquire()

    try:
      invalid_names = set(self.__names()).intersection(names)
      if invalid_names:
        # This must be an explicit raise, not an assert, because assert is
        # turned off when using optimization, and this can happen because of
        # concurrency even if the user doesn't want it.
        raise errors.LockError("duplicate add() (%s)" % invalid_names)

      for lockname in names:
        lock = SharedLock()

        if acquired:
          lock.acquire(shared=shared)
          # now the lock cannot be deleted, we have it!
          try:
            self._add_owned(name=lockname)
          except:
            # We shouldn't have problems adding the lock to the owners list,
            # but if we did we'll try to release this lock and re-raise
            # exception.  Of course something is going to be really wrong,
            # after this.  On the other hand the lock hasn't been added to the
            # __lockdict yet so no other threads should be pending on it. This
            # release is just a safety measure.
            lock.release()
            raise

        self.__lockdict[lockname] = lock

    finally:
      # Only release __lock if we were not holding it previously.
      if release_lock:
        self.__lock.release()

    return True

  def remove(self, names):
    """Remove elements from the lock set.

    You can either not hold anything in the lockset or already hold a superset
    of the elements you want to delete, exclusively.

    @param names: names of the resource to remove.

    @return: a list of locks which we removed; the list is always
        equal to the names list if we were holding all the locks
        exclusively

    """
    # Support passing in a single resource to remove rather than many
    if isinstance(names, basestring):
      names = [names]

    # If we own any subset of this lock it must be a superset of what we want
    # to delete. The ownership must also be exclusive, but that will be checked
    # by the lock itself.
    assert not self._is_owned() or self._list_owned().issuperset(names), (
      "remove() on acquired lockset while not owning all elements")

    removed = []

    for lname in names:
      # Calling delete() acquires the lock exclusively if we don't already own
      # it, and causes all pending and subsequent lock acquires to fail. It's
      # fine to call it out of order because delete() also implies release(),
      # and the assertion above guarantees that if we either already hold
      # everything we want to delete, or we hold none.
      try:
        self.__lockdict[lname].delete()
        removed.append(lname)
      except (KeyError, errors.LockError):
        # This cannot happen if we were already holding it, verify:
        assert not self._is_owned(), "remove failed while holding lockset"
      else:
        # If no LockError was raised we are the ones who deleted the lock.
        # This means we can safely remove it from lockdict, as any further or
        # pending delete() or acquire() will fail (and nobody can have the lock
        # since before our call to delete()).
        #
        # This is done in an else clause because if the exception was thrown
        # it's the job of the one who actually deleted it.
        del self.__lockdict[lname]
        # And let's remove it from our private list if we owned it.
        if self._is_owned():
          self._del_owned(name=lname)

    return removed


# Locking levels, must be acquired in increasing order.
# Current rules are:
#   - at level LEVEL_CLUSTER resides the Big Ganeti Lock (BGL) which must be
#   acquired before performing any operation, either in shared or in exclusive
#   mode. acquiring the BGL in exclusive mode is discouraged and should be
#   avoided.
#   - at levels LEVEL_NODE and LEVEL_INSTANCE reside node and instance locks.
#   If you need more than one node, or more than one instance, acquire them at
#   the same time.
LEVEL_CLUSTER = 0
LEVEL_INSTANCE = 1
LEVEL_NODE = 2

LEVELS = [LEVEL_CLUSTER,
          LEVEL_INSTANCE,
          LEVEL_NODE]

# Lock levels which are modifiable
LEVELS_MOD = [LEVEL_NODE, LEVEL_INSTANCE]

LEVEL_NAMES = {
  LEVEL_CLUSTER: "cluster",
  LEVEL_INSTANCE: "instance",
  LEVEL_NODE: "node",
  }

# Constant for the big ganeti lock
BGL = 'BGL'


class GanetiLockManager:
  """The Ganeti Locking Library

  The purpose of this small library is to manage locking for ganeti clusters
  in a central place, while at the same time doing dynamic checks against
  possible deadlocks. It will also make it easier to transition to a different
  lock type should we migrate away from python threads.

  """
  _instance = None

  def __init__(self, nodes=None, instances=None):
    """Constructs a new GanetiLockManager object.

    There should be only a GanetiLockManager object at any time, so this
    function raises an error if this is not the case.

    @param nodes: list of node names
    @param instances: list of instance names

    """
    assert self.__class__._instance is None, \
           "double GanetiLockManager instance"

    self.__class__._instance = self

    # The keyring contains all the locks, at their level and in the correct
    # locking order.
    self.__keyring = {
      LEVEL_CLUSTER: LockSet([BGL]),
      LEVEL_NODE: LockSet(nodes),
      LEVEL_INSTANCE: LockSet(instances),
    }

  def _names(self, level):
    """List the lock names at the given level.

    This can be used for debugging/testing purposes.

    @param level: the level whose list of locks to get

    """
    assert level in LEVELS, "Invalid locking level %s" % level
    return self.__keyring[level]._names()

  def _is_owned(self, level):
    """Check whether we are owning locks at the given level

    """
    return self.__keyring[level]._is_owned()

  is_owned = _is_owned

  def _list_owned(self, level):
    """Get the set of owned locks at the given level

    """
    return self.__keyring[level]._list_owned()

  def _upper_owned(self, level):
    """Check that we don't own any lock at a level greater than the given one.

    """
    # This way of checking only works if LEVELS[i] = i, which we check for in
    # the test cases.
    return utils.any((self._is_owned(l) for l in LEVELS[level + 1:]))

  def _BGL_owned(self): # pylint: disable-msg=C0103
    """Check if the current thread owns the BGL.

    Both an exclusive or a shared acquisition work.

    """
    return BGL in self.__keyring[LEVEL_CLUSTER]._list_owned()

  @staticmethod
  def _contains_BGL(level, names): # pylint: disable-msg=C0103
    """Check if the level contains the BGL.

    Check if acting on the given level and set of names will change
    the status of the Big Ganeti Lock.

    """
    return level == LEVEL_CLUSTER and (names is None or BGL in names)

  def acquire(self, level, names, timeout=None, shared=0):
    """Acquire a set of resource locks, at the same level.

    @param level: the level at which the locks shall be acquired;
        it must be a member of LEVELS.
    @param names: the names of the locks which shall be acquired
        (special lock names, or instance/node names)
    @param shared: whether to acquire in shared mode; by default
        an exclusive lock will be acquired
    @type timeout: float
    @param timeout: Maximum time to acquire all locks

    """
    assert level in LEVELS, "Invalid locking level %s" % level

    # Check that we are either acquiring the Big Ganeti Lock or we already own
    # it. Some "legacy" opcodes need to be sure they are run non-concurrently
    # so even if we've migrated we need to at least share the BGL to be
    # compatible with them. Of course if we own the BGL exclusively there's no
    # point in acquiring any other lock, unless perhaps we are half way through
    # the migration of the current opcode.
    assert (self._contains_BGL(level, names) or self._BGL_owned()), (
            "You must own the Big Ganeti Lock before acquiring any other")

    # Check we don't own locks at the same or upper levels.
    assert not self._upper_owned(level), ("Cannot acquire locks at a level"
           " while owning some at a greater one")

    # Acquire the locks in the set.
    return self.__keyring[level].acquire(names, shared=shared, timeout=timeout)

  def release(self, level, names=None):
    """Release a set of resource locks, at the same level.

    You must have acquired the locks, either in shared or in exclusive
    mode, before releasing them.

    @param level: the level at which the locks shall be released;
        it must be a member of LEVELS
    @param names: the names of the locks which shall be released
        (defaults to all the locks acquired at that level)

    """
    assert level in LEVELS, "Invalid locking level %s" % level
    assert (not self._contains_BGL(level, names) or
            not self._upper_owned(LEVEL_CLUSTER)), (
            "Cannot release the Big Ganeti Lock while holding something"
            " at upper levels (%r)" %
            (utils.CommaJoin(["%s=%r" % (LEVEL_NAMES[i], self._list_owned(i))
                              for i in self.__keyring.keys()]), ))

    # Release will complain if we don't own the locks already
    return self.__keyring[level].release(names)

  def add(self, level, names, acquired=0, shared=0):
    """Add locks at the specified level.

    @param level: the level at which the locks shall be added;
        it must be a member of LEVELS_MOD.
    @param names: names of the locks to acquire
    @param acquired: whether to acquire the newly added locks
    @param shared: whether the acquisition will be shared

    """
    assert level in LEVELS_MOD, "Invalid or immutable level %s" % level
    assert self._BGL_owned(), ("You must own the BGL before performing other"
           " operations")
    assert not self._upper_owned(level), ("Cannot add locks at a level"
           " while owning some at a greater one")
    return self.__keyring[level].add(names, acquired=acquired, shared=shared)

  def remove(self, level, names):
    """Remove locks from the specified level.

    You must either already own the locks you are trying to remove
    exclusively or not own any lock at an upper level.

    @param level: the level at which the locks shall be removed;
        it must be a member of LEVELS_MOD
    @param names: the names of the locks which shall be removed
        (special lock names, or instance/node names)

    """
    assert level in LEVELS_MOD, "Invalid or immutable level %s" % level
    assert self._BGL_owned(), ("You must own the BGL before performing other"
           " operations")
    # Check we either own the level or don't own anything from here
    # up. LockSet.remove() will check the case in which we don't own
    # all the needed resources, or we have a shared ownership.
    assert self._is_owned(level) or not self._upper_owned(level), (
           "Cannot remove locks at a level while not owning it or"
           " owning some at a greater one")
    return self.__keyring[level].remove(names)
