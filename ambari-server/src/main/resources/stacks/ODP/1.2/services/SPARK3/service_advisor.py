#!/usr/bin/env ambari-python-wrap
"""
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
"""

# Python imports
import importlib.util
import os
import traceback
import re
import socket
import fnmatch
import xml.etree.ElementTree as ET


from resource_management.core.logger import Logger

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STACKS_DIR = os.path.join(SCRIPT_DIR, '../../../../../stacks/')
PARENT_FILE = os.path.join(STACKS_DIR, 'service_advisor.py')

try:
  if "BASE_SERVICE_ADVISOR" in os.environ:
    PARENT_FILE = os.environ["BASE_SERVICE_ADVISOR"]
  with open(PARENT_FILE, 'rb') as fp:
    spec = importlib.util.spec_from_file_location('service_advisor', PARENT_FILE)
    service_advisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service_advisor)

except Exception as e:
  traceback.print_exc()
  print("Failed to load parent")

class Spark3ServiceAdvisor(service_advisor.ServiceAdvisor):

  def __init__(self, *args, **kwargs):
    self.as_super = super(Spark3ServiceAdvisor, self)
    self.as_super.__init__(*args, **kwargs)

    # Always call these methods
    self.modifyMastersWithMultipleInstances()
    self.modifyCardinalitiesDict()
    self.modifyHeapSizeProperties()
    self.modifyNotValuableComponents()
    self.modifyComponentsNotPreferableOnServer()
    self.modifyComponentLayoutSchemes()

  def modifyMastersWithMultipleInstances(self):
    """
    Modify the set of masters with multiple instances.
    Must be overriden in child class.
    """
    # Nothing to do
    pass

  def modifyCardinalitiesDict(self):
    """
    Modify the dictionary of cardinalities.
    Must be overriden in child class.
    """
    # Nothing to do
    pass

  def modifyHeapSizeProperties(self):
    """
    Modify the dictionary of heap size properties.
    Must be overriden in child class.

    """
    self.heap_size_properties = {"SPARK3_JOBHISTORYSERVER":
                                   [{"config-name": "spark3-env",
                                     "property": "spark_daemon_memory",
                                     "default": "2048m"}]}

  def modifyNotValuableComponents(self):
    """
    Modify the set of components whose host assignment is based on other services.
    Must be overriden in child class.
    """
    # Nothing to do
    pass

  def modifyComponentsNotPreferableOnServer(self):
    """
    Modify the set of components that are not preferable on the server.
    Must be overriden in child class.
    """
    # Nothing to do
    pass

  def modifyComponentLayoutSchemes(self):
    """
    Modify layout scheme dictionaries for components.
    The scheme dictionary basically maps the number of hosts to
    host index where component should exist.
    Must be overriden in child class.
    """
    # Nothing to do
    pass

  def getServiceComponentLayoutValidations(self, services, hosts):
    """
    Get a list of errors.
    Must be overriden in child class.
    """

    return self.getServiceComponentCardinalityValidations(services, hosts, "SPARK3")

  def getServiceConfigurationRecommendations(self, configurations, clusterData, services, hosts):
    """
    Entry point.
    Must be overriden in child class.
    """
    #Logger.info("Class: %s, Method: %s. Recommending Service Configurations." %
    #            (self.__class__.__name__, inspect.stack()[0][3]))

    recommender = Spark3Recommender()
    recommender.recommendSpark3ConfigurationsFromHDP25(configurations, clusterData, services, hosts)
    recommender.recommendSPARK3ConfigurationsFromHDP26(configurations, clusterData, services, hosts)



  def getServiceConfigurationsValidationItems(self, configurations, recommendedDefaults, services, hosts):
    """
    Entry point.
    Validate configurations for the service. Return a list of errors.
    The code for this function should be the same for each Service Advisor.
    """
    #Logger.info("Class: %s, Method: %s. Validating Configurations." %
    #            (self.__class__.__name__, inspect.stack()[0][3]))

    validator = Spark3Validator()
    # Calls the methods of the validator using arguments,
    # method(siteProperties, siteRecommendations, configurations, services, hosts)
    return validator.validateListOfConfigUsingMethod(configurations, recommendedDefaults, services, hosts, validator.validators)

  def isComponentUsingCardinalityForLayout(self, componentName):
    return componentName in ('SPARK3_THRIFTSERVER', 'SPARK3_LIVY2_SERVER')

  @staticmethod
  def isKerberosEnabled(services, configurations):
    """
    Determines if security is enabled by testing the value of spark3-defaults/spark.history.kerberos.enabled enabled.
    If the property exists and is equal to "true", then is it enabled; otherwise is it assumed to be
    disabled.

    :type services: dict
    :param services: the dictionary containing the existing configuration values
    :type configurations: dict
    :param configurations: the dictionary containing the updated configuration values
    :rtype: bool
    :return: True or False
    """
    if configurations and "spark3-defaults" in configurations and \
            "spark.history.kerberos.enabled" in configurations["spark3-defaults"]["properties"]:
      return configurations["spark3-defaults"]["properties"]["spark.history.kerberos.enabled"].lower() == "true"
    elif services and "spark3-defaults" in services["configurations"] and \
            "spark.history.kerberos.enabled" in services["configurations"]["spark3-defaults"]["properties"]:
      return services["configurations"]["spark3-defaults"]["properties"]["spark.history.kerberos.enabled"].lower() == "true"
    else:
      return False


class Spark3Recommender(service_advisor.ServiceAdvisor):
  """
  Spark3 Recommender suggests properties when adding the service for the first time or modifying configs via the UI.
  """

  def __init__(self, *args, **kwargs):
    self.as_super = super(Spark3Recommender, self)
    self.as_super.__init__(*args, **kwargs)

  def recommendSpark3ConfigurationsFromHDP25(self, configurations, clusterData, services, hosts):
    """
    :type configurations dict
    :type clusterData dict
    :type services dict
    :type hosts dict
    """
    putSparkProperty = self.putProperty(configurations, "spark3-defaults", services)
    putSparkThriftSparkConf = self.putProperty(configurations, "spark3-thrift-sparkconf", services)

    spark_queue = self.recommendYarnQueue(services, "spark3-defaults", "spark.yarn.queue")
    if spark_queue is not None:
      putSparkProperty("spark.yarn.queue", spark_queue)

    spark_thrift_queue = self.recommendYarnQueue(services, "spark3-thrift-sparkconf", "spark.yarn.queue")
    if spark_thrift_queue is not None:
      putSparkThriftSparkConf("spark.yarn.queue", spark_thrift_queue)


  def recommendSPARK3ConfigurationsFromHDP26(self, configurations, clusterData, services, hosts):
    """
    :type configurations dict
    :type clusterData dict
    :type services dict
    :type hosts dict
    """

    if Spark3ServiceAdvisor.isKerberosEnabled(services, configurations):

      spark3_defaults = self.getServicesSiteProperties(services, "spark3-defaults")

      if spark3_defaults:
        putSpark3DafaultsProperty = self.putProperty(configurations, "spark3-defaults", services)
        putSpark3DafaultsProperty('spark.acls.enable', 'true')
        putSpark3DafaultsProperty('spark.admin.acls', '')
        putSpark3DafaultsProperty('spark.history.ui.acls.enable', 'true')
        putSpark3DafaultsProperty('spark.history.ui.admin.acls', '')


    self.__addZeppelinToLivy2SuperUsers(configurations, services)


  def __addZeppelinToLivy2SuperUsers(self, configurations, services):
    """
    If Kerberos is enabled AND Zeppelin is installed AND Spark3 Livy Server is installed, then set
    spark3-livy2-conf/livy.superusers to contain the Zeppelin principal name from
    zeppelin-site/zeppelin.server.kerberos.principal

    :param configurations:
    :param services:
    """
    if Spark3ServiceAdvisor.isKerberosEnabled(services, configurations):
      zeppelin_site = self.getServicesSiteProperties(services, "zeppelin-site")

      if zeppelin_site and 'zeppelin.server.kerberos.principal' in zeppelin_site:
        zeppelin_principal = zeppelin_site['zeppelin.server.kerberos.principal']
        zeppelin_user = zeppelin_principal.split('@')[0] if zeppelin_principal else None

        if zeppelin_user:
          livy2_conf = self.getServicesSiteProperties(services, 'spark3-livy2-conf')

          if livy2_conf:
            superusers = livy2_conf['livy.superusers'] if livy2_conf and 'livy.superusers' in livy2_conf else None

            # add the Zeppelin user to the set of users
            if superusers:
              _superusers = superusers.split(',')
              _superusers = [x.strip() for x in _superusers]
              _superusers = list(filter(None, _superusers))  # Removes empty string elements from array
            else:
              _superusers = []

            if zeppelin_user not in _superusers:
              _superusers.append(zeppelin_user)

              putLivy2ConfProperty = self.putProperty(configurations, 'spark3-livy2-conf', services)
              putLivy2ConfProperty('livy.superusers', ','.join(_superusers))


class Spark3Validator(service_advisor.ServiceAdvisor):
  """
  Spark3 Validator checks the correctness of properties whenever the service is first added or the user attempts to
  change configs via the UI.
  """

  def __init__(self, *args, **kwargs):
    self.as_super = super(Spark3Validator, self)
    self.as_super.__init__(*args, **kwargs)

    self.validators = [("spark3-defaults", self.validateSpark3DefaultsFromHDP25),
                       ("spark3-thrift-sparkconf", self.validateSpark3ThriftSparkConfFromHDP25)]


  def validateSpark3DefaultsFromHDP25(self, properties, recommendedDefaults, configurations, services, hosts):
    validationItems = [
      {
        "config-name": 'spark.yarn.queue',
        "item": self.validatorYarnQueue(properties, recommendedDefaults, 'spark.yarn.queue', services)
      }
    ]
    return self.toConfigurationValidationProblems(validationItems, "spark3-defaults")


  def validateSpark3ThriftSparkConfFromHDP25(self, properties, recommendedDefaults, configurations, services, hosts):
    validationItems = [
      {
        "config-name": 'spark.yarn.queue',
        "item": self.validatorYarnQueue(properties, recommendedDefaults, 'spark.yarn.queue', services)
      }
    ]
    return self.toConfigurationValidationProblems(validationItems, "spark3-thrift-sparkconf")
