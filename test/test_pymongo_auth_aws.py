# Copyright 2020-present MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test the pymongo-auth-aws module."""

from datetime import datetime, timedelta
import os
import sys

sys.path[0:0] = [""]

import bson
from bson import Binary
import pymongo_auth_aws
import requests_mock

from pymongo_auth_aws import auth
from pymongo_auth_aws.auth import _get_region, _aws_temp_credentials, AwsSaslContext, AwsCredential
from pymongo_auth_aws.errors import PyMongoAuthAwsError

from test import unittest


AWS_DATE_FORMAT = r"%Y-%m-%dT%H:%M:%SZ"

class TestAuthAws(unittest.TestCase):

    def assertVersionLike(self, version):
        self.assertTrue(isinstance(version, str), msg=version)
        # There should be at least one dot: "1.0" or "1.0.0" not "1".
        self.assertGreaterEqual(len(version.split('.')), 2, msg=version)

    def test_version(self):
        self.assertVersionLike(pymongo_auth_aws.__version__)

    def test_region(self):
        # Default region is us-east-1.
        self.assertEqual('us-east-1', _get_region('sts.amazonaws.com'))
        self.assertEqual('us-east-1', _get_region('first'))
        self.assertEqual('us-east-1', _get_region('f'))
        # Otherwise, the region is the second label.
        self.assertEqual('second', _get_region('first.second'))
        self.assertEqual('second', _get_region('first.second.third'))
        self.assertEqual('second', _get_region('sts.second.amazonaws.com'))
        # Assert invalid hosts cause an error.
        self.assertRaises(PyMongoAuthAwsError, _get_region, '')
        self.assertRaises(PyMongoAuthAwsError, _get_region, 'i'*256)
        self.assertRaises(PyMongoAuthAwsError, _get_region, 'first..second')
        self.assertRaises(PyMongoAuthAwsError, _get_region, '.first.second')
        self.assertRaises(PyMongoAuthAwsError, _get_region, 'first.second.')

    def ensure_equal(self, creds, expected):
        self.assertEqual(creds.username, expected['AccessKeyId'])
        self.assertEqual(creds.password, expected['SecretAccessKey'])
        self.assertEqual(creds.token, expected['Token'])
        self.assertEqual(creds.expiration, expected['Expiration'])

    def test_aws_temp_credentials_env_variables(self):
        os.environ['AWS_ACCESS_KEY_ID'] = 'foo'
        os.environ['AWS_SECRET_ACCESS_KEY'] = 'bar'
        creds = _aws_temp_credentials()
        del os.environ['AWS_ACCESS_KEY_ID']
        del os.environ['AWS_SECRET_ACCESS_KEY']
        self.assertEqual(creds.username, 'foo')
        self.assertEqual(creds.password, 'bar')
        self.assertEqual(creds.token, None)
        self.assertEqual(creds.expiration, None)

    def test_aws_temp_credentials_relative_url(self):
        os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'] = 'foo'
        expected = dict(AccessKeyId='foo', SecretAccessKey='bar', Token='fizz', Expiration='2016-03-15T00:05:07Z')
        with requests_mock.Mocker() as m:
            m.get('%sfoo' % auth._AWS_REL_URI, json=expected)
            creds = _aws_temp_credentials()
        del os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI']
        self.ensure_equal(creds, expected)

    def test_aws_temp_credentials_ec2(self):
        expected = dict(AccessKeyId='foo', SecretAccessKey='bar', Token='fizz', Expiration='2016-03-15T00:05:07Z')
        with requests_mock.Mocker() as m:
            m.put('%slatest/api/token' % auth._AWS_EC2_URI, text='foo')
            m.get('%s%s' % (auth._AWS_EC2_URI, auth._AWS_EC2_PATH), text='bar')
            m.get('%s%sbar' % (auth._AWS_EC2_URI, auth._AWS_EC2_PATH), json=expected)
            creds = _aws_temp_credentials()
        self.ensure_equal(creds, expected)

    def test_cache_credentials(self):
        os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'] = 'foo'
        tomorrow = datetime.now(auth.utc) + timedelta(days=1)
        expected = dict(AccessKeyId='foo', SecretAccessKey='bar', Token='fizz', Expiration=tomorrow.strftime(AWS_DATE_FORMAT))
        with requests_mock.Mocker() as m:
            m.get('%sfoo' % auth._AWS_REL_URI, json=expected)
            creds = _aws_temp_credentials()
        self.ensure_equal(creds, expected)

        creds = _aws_temp_credentials(creds)
        self.ensure_equal(creds, expected)

        del os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI']
        auth._cached_credential = None

    def test_cache_expired(self):
        os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'] = 'foo'
        expired = datetime.now(auth.utc) - timedelta(hours=1)
        expected = dict(AccessKeyId='foo', SecretAccessKey='bar', Token='fizz', Expiration=expired.strftime(AWS_DATE_FORMAT))
        with requests_mock.Mocker() as m:
            m.get('%sfoo' % auth._AWS_REL_URI, json=expected)
            creds = _aws_temp_credentials()

        self.ensure_equal(creds, expected)

        expected['AccessKeyId'] = 'fizz'
        with requests_mock.Mocker() as m:
            m.get('%sfoo' % auth._AWS_REL_URI, json=expected)
            creds = _aws_temp_credentials()

        self.ensure_equal(creds, expected)

        del os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI']

    def test_cache_expires_soon(self):
        os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'] = 'foo'
        soon = datetime.now(auth.utc) + timedelta(minutes=1)
        expected = dict(AccessKeyId='foo', SecretAccessKey='bar', Token='fizz', Expiration=soon.strftime(AWS_DATE_FORMAT))
        with requests_mock.Mocker() as m:
            m.get('%sfoo' % auth._AWS_REL_URI, json=expected)
            creds = _aws_temp_credentials()

        self.ensure_equal(creds, expected)

        expected['AccessKeyId'] = 'fizz'
        with requests_mock.Mocker() as m:
            m.get('%sfoo' % auth._AWS_REL_URI, json=expected)
            creds = _aws_temp_credentials(creds)

        self.ensure_equal(creds, expected)

        del os.environ['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI']



class _AwsSaslContext(AwsSaslContext):

    def binary_type(self):
        """Return the bson.binary.Binary type."""
        return Binary

    def bson_encode(self, doc):
        """Encode a dictionary to BSON."""
        return bson.encode(doc)

    def bson_decode(self, data):
        """Decode BSON to a dictionary."""
        return bson.decode(data)


# Support Python 2.7 unicode inputs.
if sys.version_info[0] == 3:
    unicode = str


class TestAwsSaslContext(unittest.TestCase):

    def test_step(self):
        creds = AwsCredential('foo', 'bar', 'baz', None)
        test = _AwsSaslContext(creds)
        response = bson.decode(test.step(None))
        nonce = response['r'] + os.urandom(32)

        payload = bson.encode(dict(s=nonce, h=unicode('foo.com')))
        response = test.step(payload)
        self.assertIsInstance(response, Binary)



if __name__ == "__main__":
    unittest.main()
