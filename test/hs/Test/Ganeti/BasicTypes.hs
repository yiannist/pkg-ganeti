{-# LANGUAGE TemplateHaskell, FlexibleInstances, TypeSynonymInstances #-}
{-# OPTIONS_GHC -fno-warn-orphans #-}

{-| Unittests for ganeti-htools.

-}

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

module Test.Ganeti.BasicTypes (testBasicTypes) where

import Test.QuickCheck hiding (Result)
import Test.QuickCheck.Function

import Control.Applicative
import Control.Monad

import Test.Ganeti.TestHelper
import Test.Ganeti.TestCommon

import Ganeti.BasicTypes

-- Since we actually want to test these, don't tell us not to use them :)

{-# ANN module "HLint: ignore Functor law" #-}
{-# ANN module "HLint: ignore Monad law, left identity" #-}
{-# ANN module "HLint: ignore Monad law, right identity" #-}
{-# ANN module "HLint: ignore Use >=>" #-}
{-# ANN module "HLint: ignore Use ." #-}

-- * Arbitrary instances

instance (Arbitrary a) => Arbitrary (Result a) where
  arbitrary = oneof [ Bad <$> arbitrary
                    , Ok  <$> arbitrary
                    ]

-- * Test cases

-- | Tests the functor identity law:
--
-- > fmap id == id
prop_functor_id :: Result Int -> Property
prop_functor_id ri =
  fmap id ri ==? ri

-- | Tests the functor composition law:
--
-- > fmap (f . g)  ==  fmap f . fmap g
prop_functor_composition :: Result Int
                         -> Fun Int Int -> Fun Int Int -> Property
prop_functor_composition ri (Fun _ f) (Fun _ g) =
  fmap (f . g) ri ==? (fmap f . fmap g) ri

-- | Tests the applicative identity law:
--
-- > pure id <*> v = v
prop_applicative_identity :: Result Int -> Property
prop_applicative_identity v =
  pure id <*> v ==? v

-- | Tests the applicative composition law:
--
-- > pure (.) <*> u <*> v <*> w = u <*> (v <*> w)
prop_applicative_composition :: Result (Fun Int Int)
                             -> Result (Fun Int Int)
                             -> Result Int
                             -> Property
prop_applicative_composition u v w =
  let u' = fmap apply u
      v' = fmap apply v
  in pure (.) <*> u' <*> v' <*> w ==? u' <*> (v' <*> w)

-- | Tests the applicative homomorphism law:
--
-- > pure f <*> pure x = pure (f x)
prop_applicative_homomorphism :: Fun Int Int -> Int -> Property
prop_applicative_homomorphism (Fun _ f) x =
  ((pure f <*> pure x)::Result Int) ==? pure (f x)

-- | Tests the applicative interchange law:
--
-- > u <*> pure y = pure ($ y) <*> u
prop_applicative_interchange :: Result (Fun Int Int)
                             -> Int -> Property
prop_applicative_interchange f y =
  let u = fmap apply f -- need to extract the actual function from Fun
  in u <*> pure y ==? pure ($ y) <*> u

-- | Tests the applicative\/functor correspondence:
--
-- > fmap f x = pure f <*> x
prop_applicative_functor :: Fun Int Int -> Result Int -> Property
prop_applicative_functor (Fun _ f) x =
  fmap f x ==? pure f <*> x

-- | Tests the applicative\/monad correspondence:
--
-- > pure = return
--
-- > (<*>) = ap
prop_applicative_monad :: Int -> Result (Fun Int Int) -> Property
prop_applicative_monad v f =
  let v' = pure v :: Result Int
      f' = fmap apply f -- need to extract the actual function from Fun
  in v' ==? return v .&&. (f' <*> v') ==? f' `ap` v'

-- | Tests the monad laws:
--
-- > return a >>= k == k a
--
-- > m >>= return == m
--
-- > m >>= (\x -> k x >>= h) == (m >>= k) >>= h
prop_monad_laws :: Int -> Result Int
                -> Fun Int (Result Int)
                -> Fun Int (Result Int)
                -> Property
prop_monad_laws a m (Fun _ k) (Fun _ h) =
  conjoin
  [ printTestCase "return a >>= k == k a" ((return a >>= k) ==? k a)
  , printTestCase "m >>= return == m" ((m >>= return) ==? m)
  , printTestCase "m >>= (\\x -> k x >>= h) == (m >>= k) >>= h)"
    ((m >>= (\x -> k x >>= h)) ==? ((m >>= k) >>= h))
  ]

-- | Tests the monad plus laws:
--
-- > mzero >>= f = mzero
--
-- > v >> mzero = mzero
prop_monadplus_mzero :: Result Int -> Fun Int (Result Int) -> Property
prop_monadplus_mzero v (Fun _ f) =
  printTestCase "mzero >>= f = mzero" ((mzero >>= f) ==? mzero) .&&.
  -- FIXME: since we have "many" mzeros, we can't test for equality,
  -- just that we got back a 'Bad' value; I'm not sure if this means
  -- our MonadPlus instance is not sound or not...
  printTestCase "v >> mzero = mzero" (isBad (v >> mzero))

testSuite "BasicTypes"
  [ 'prop_functor_id
  , 'prop_functor_composition
  , 'prop_applicative_identity
  , 'prop_applicative_composition
  , 'prop_applicative_homomorphism
  , 'prop_applicative_interchange
  , 'prop_applicative_functor
  , 'prop_applicative_monad
  , 'prop_monad_laws
  , 'prop_monadplus_mzero
  ]
