#!/usr/bin/env python

'''
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''
import json
from mock.mock import MagicMock, call, patch
from stacks.utils.RMFTestCase import *

class TestHiveClient(RMFTestCase):
  COMMON_SERVICES_PACKAGE_DIR = "HIVE/0.12.0.2.0/package"
  STACK_VERSION = "2.0.6"

  CONFIG_OVERRIDES = { "serviceName" : "HIVE", "role" : "HIVE_CLIENT" }

  def test_configure_default(self):
    self.executeScript(self.COMMON_SERVICES_PACKAGE_DIR + "/scripts/hive_client.py",
                       classname = "HiveClient",
                       command = "configure",
                       config_file="default_client.json",
                       config_overrides = self.CONFIG_OVERRIDES,
                       stack_version = self.STACK_VERSION,
                       target = RMFTestCase.TARGET_COMMON_SERVICES
    )
    self.assertResourceCalled('Directory', '/etc/hive',
        mode = 0o755
    )
    self.assertResourceCalled('Directory', '/usr/hdp/current/hive-client/conf',
        owner = 'hive',
        group = 'hadoop',
        create_parents = True,
        mode = 0o755,
    )
    self.assertResourceCalled('XmlConfig', 'mapred-site.xml',
        group = 'hadoop',
        conf_dir = '/usr/hdp/current/hive-client/conf',
        mode = 0o644,
        configuration_attributes = self.getConfig()['configurationAttributes']['mapred-site'],
        owner = 'hive',
        configurations = self.getConfig()['configurations']['mapred-site'],
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-default.xml.template',
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-env.sh.template',
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-exec-log4j.properties',
        content = InlineTemplate('log4jproperties\nline2'),
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-log4j.properties',
        content = InlineTemplate('log4jproperties\nline2'),
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('XmlConfig', 'hive-site.xml',
                              group = 'hadoop',
                              conf_dir = '/usr/hdp/current/hive-client/conf',
                              mode = 0o644,
                              configuration_attributes = self.getConfig()['configurationAttributes']['hive-site'],
                              owner = 'hive',
                              configurations = self.getConfig()['configurations']['hive-site'],
                              )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-env.sh',
                              content = InlineTemplate(self.getConfig()['configurations']['hive-env']['content']),
                              owner = 'hive',
                              group = 'hadoop',
                              mode = 0o644,
                              )
    self.assertResourceCalled('Directory', '/etc/security/limits.d',
                              owner = 'root',
                              group = 'root',
                              create_parents = True,
                              )
    self.assertResourceCalled('File', '/etc/security/limits.d/hive.conf',
                              content = Template('hive.conf.j2'),
                              owner = 'root',
                              group = 'root',
                              mode = 0o644,
                              )
    self.assertResourceCalled('File', '/usr/lib/ambari-agent/DBConnectionVerification.jar',
        content = DownloadSource('http://c6401.ambari.apache.org:8080/resources/DBConnectionVerification.jar'),
        mode = 0o644,
    )
    self.assertNoMoreResources()



  def test_configure_secured(self):
    self.executeScript(self.COMMON_SERVICES_PACKAGE_DIR + "/scripts/hive_client.py",
                       classname = "HiveClient",
                       command = "configure",
                       config_file="secured_client.json",
                       config_overrides = self.CONFIG_OVERRIDES,
                       stack_version = self.STACK_VERSION,
                       target = RMFTestCase.TARGET_COMMON_SERVICES
    )
    self.assertResourceCalled('Directory', '/etc/hive',
        mode = 0o755
    )
    self.assertResourceCalled('Directory', '/usr/hdp/current/hive-client/conf',
        owner = 'hive',
        group = 'hadoop',
        create_parents = True,
        mode = 0o755,
    )
    self.assertResourceCalled('XmlConfig', 'mapred-site.xml',
        group = 'hadoop',
        conf_dir = '/usr/hdp/current/hive-client/conf',
        mode = 0o644,
        configuration_attributes = self.getConfig()['configurationAttributes']['mapred-site'],
        owner = 'hive',
        configurations = self.getConfig()['configurations']['mapred-site'],
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-default.xml.template',
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-env.sh.template',
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-exec-log4j.properties',
        content = InlineTemplate('log4jproperties\nline2'),
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-log4j.properties',
        content = InlineTemplate('log4jproperties\nline2'),
        owner = 'hive',
        group = 'hadoop',
        mode = 0o644,
    )
    self.assertResourceCalled('XmlConfig', 'hive-site.xml',
                              group = 'hadoop',
                              conf_dir = '/usr/hdp/current/hive-client/conf',
                              mode = 0o644,
                              configuration_attributes = self.getConfig()['configurationAttributes']['hive-site'],
                              owner = 'hive',
                              configurations = self.getConfig()['configurations']['hive-site'],
                              )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/hive-env.sh',
                              content = InlineTemplate(self.getConfig()['configurations']['hive-env']['content']),
                              owner = 'hive',
                              group = 'hadoop',
                              mode = 0o644,
                              )
    self.assertResourceCalled('Directory', '/etc/security/limits.d',
                              owner = 'root',
                              group = 'root',
                              create_parents = True,
                              )
    self.assertResourceCalled('File', '/etc/security/limits.d/hive.conf',
                              content = Template('hive.conf.j2'),
                              owner = 'root',
                              group = 'root',
                              mode = 0o644,
                              )
    self.assertResourceCalled('File', '/usr/hdp/current/hive-client/conf/zkmigrator_jaas.conf',
                              content = Template('zkmigrator_jaas.conf.j2'),
                              owner = 'hive',
                              group = 'hadoop',
                              )
    self.assertResourceCalled('File', '/usr/lib/ambari-agent/DBConnectionVerification.jar',
        content = DownloadSource('http://c6401.ambari.apache.org:8080/resources/DBConnectionVerification.jar'),
        mode = 0o644,
    )
    self.assertNoMoreResources()

  def test_pre_upgrade_restart(self):
    config_file = self.get_src_folder()+"/test/python/stacks/2.0.6/configs/default.json"
    with open(config_file, "r") as f:
      json_content = json.load(f)
    version = '2.2.1.0-3242'
    json_content['commandParams']['version'] = version
    self.executeScript(self.COMMON_SERVICES_PACKAGE_DIR + "/scripts/hive_client.py",
                       classname = "HiveClient",
                       command = "pre_upgrade_restart",
                       config_dict = json_content,
                       config_overrides = self.CONFIG_OVERRIDES,
                       stack_version = self.STACK_VERSION,
                       target = RMFTestCase.TARGET_COMMON_SERVICES)
    self.assertResourceCalled('Execute',
                              ('ambari-python-wrap', '/usr/bin/hdp-select', 'set', 'hadoop-client', version), sudo=True,)
    self.assertNoMoreResources()

  @patch("os.path.exists")
  @patch("resource_management.core.shell.call")
  def test_pre_upgrade_restart_23(self, call_mock, os_path__exists_mock):
    config_file = self.get_src_folder()+"/test/python/stacks/2.0.6/configs/default.json"
    os_path__exists_mock.return_value = False
    with open(config_file, "r") as f:
      json_content = json.load(f)
    version = '2.3.0.0-1234'
    json_content['commandParams']['version'] = version

    mocks_dict = {}
    self.executeScript(self.COMMON_SERVICES_PACKAGE_DIR + "/scripts/hive_client.py",
                       classname = "HiveClient",
                       command = "pre_upgrade_restart",
                       config_dict = json_content,
                       config_overrides = self.CONFIG_OVERRIDES,
                       stack_version = self.STACK_VERSION,
                       target = RMFTestCase.TARGET_COMMON_SERVICES,
                       mocks_dict = mocks_dict)

    self.assertResourceCalledIgnoreEarlier('Execute',
                              ('ambari-python-wrap', '/usr/bin/hdp-select', 'set', 'hadoop-client', version), sudo=True,)
    self.assertNoMoreResources()
