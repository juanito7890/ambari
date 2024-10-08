/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
package org.apache.ambari.logsearch.conf;

import static javax.ws.rs.core.Response.Status.SERVICE_UNAVAILABLE;
import static org.apache.ambari.logsearch.common.LogSearchConstants.LOGSEARCH_SESSION_ID;

import java.io.File;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.List;

import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.apache.ambari.logsearch.common.LogSearchLdapAuthorityMapper;
import org.apache.ambari.logsearch.common.StatusMessage;
import org.apache.ambari.logsearch.conf.global.LogLevelFilterManagerState;
import org.apache.ambari.logsearch.conf.global.LogSearchConfigState;
import org.apache.ambari.logsearch.conf.global.SolrCollectionState;
import org.apache.ambari.logsearch.dao.RoleDao;
import org.apache.ambari.logsearch.web.authenticate.LogsearchAuthFailureHandler;
import org.apache.ambari.logsearch.web.authenticate.LogsearchAuthSuccessHandler;
import org.apache.ambari.logsearch.web.authenticate.LogsearchLogoutSuccessHandler;
import org.apache.ambari.logsearch.web.filters.ConfigStateProvider;
import org.apache.ambari.logsearch.web.filters.GlobalStateProvider;
import org.apache.ambari.logsearch.web.filters.LogsearchAuthenticationEntryPoint;
import org.apache.ambari.logsearch.web.filters.LogsearchCorsFilter;
import org.apache.ambari.logsearch.web.filters.LogsearchFilter;
import org.apache.ambari.logsearch.web.filters.LogsearchJWTFilter;
import org.apache.ambari.logsearch.web.filters.LogsearchKRBAuthenticationFilter;
import org.apache.ambari.logsearch.web.filters.LogsearchSecurityContextFormationFilter;
import org.apache.ambari.logsearch.web.filters.LogsearchTrustedProxyFilter;
import org.apache.ambari.logsearch.web.filters.LogsearchUsernamePasswordAuthenticationFilter;
import org.apache.ambari.logsearch.web.security.LogsearchAuthenticationProvider;
import org.apache.ambari.logsearch.web.security.LogsearchLdapAuthenticationProvider;
import org.apache.commons.io.FileUtils;
import org.apache.commons.lang.StringUtils;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.springframework.context.annotation.Bean;
import org.springframework.ldap.core.LdapTemplate;
import org.springframework.context.annotation.Configuration;
import org.springframework.ldap.core.support.LdapContextSource;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;
import org.springframework.security.ldap.authentication.BindAuthenticator;
import org.springframework.security.ldap.authentication.NullLdapAuthoritiesPopulator;
import org.springframework.security.ldap.search.FilterBasedLdapUserSearch;
import org.springframework.security.ldap.userdetails.DefaultLdapAuthoritiesPopulator;
import org.springframework.security.ldap.userdetails.LdapAuthoritiesPopulator;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.access.intercept.FilterSecurityInterceptor;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;
import org.springframework.security.web.header.HeaderWriter;
import org.springframework.security.web.header.writers.HstsHeaderWriter;
import org.springframework.security.web.header.writers.StaticHeadersWriter;
import org.springframework.security.web.header.writers.XContentTypeOptionsHeaderWriter;
import org.springframework.security.web.header.writers.XXssProtectionHeaderWriter;
import org.springframework.security.web.header.writers.frameoptions.XFrameOptionsHeaderWriter;
import org.springframework.security.web.util.matcher.AntPathRequestMatcher;
import org.springframework.security.web.util.matcher.OrRequestMatcher;
import org.springframework.security.web.util.matcher.RequestMatcher;

import com.google.common.collect.Lists;

@Configuration
@EnableWebSecurity
public class SecurityConfig extends WebSecurityConfigurerAdapter {

  private static final Logger logger = LogManager.getLogger(SecurityConfig.class);

  @Inject
  private AuthPropsConfig authPropsConfig;

  @Inject
  private LogSearchHttpHeaderConfig logSearchHttpHeaderConfig;

  @Inject
  private LogSearchHttpConfig logSearchHttpConfig;

  @Inject
  private LogSearchSslConfig logSearchSslConfig;

  @Inject
  private SolrServiceLogPropsConfig solrServiceLogPropsConfig;

  @Inject
  private SolrAuditLogPropsConfig solrAuditLogPropsConfig;

  @Inject
  private SolrMetadataPropsConfig solrEventHistoryPropsConfig;

  @Inject
  @Named("solrServiceLogsState")
  private SolrCollectionState solrServiceLogsState;

  @Inject
  @Named("solrAuditLogsState")
  private SolrCollectionState solrAuditLogsState;

  @Inject
  @Named("solrMetadataState")
  private SolrCollectionState solrMetadataState;

  @Inject
  @Named("logLevelFilterManagerState")
  private LogLevelFilterManagerState logLevelFilterManagerState;

  @Inject
  private LogSearchConfigState logSearchConfigState;

  @Inject
  private LogSearchConfigApiConfig logSearchConfigApiConfig;

  @Inject
  private LogsearchAuthenticationProvider logsearchAuthenticationProvider;

  @Inject
  private RoleDao roleDao;

  @Override
  protected void configure(HttpSecurity http) throws Exception {
    http
      .headers()
        .addHeaderWriter(
          new LogSearchCompositeHeaderWriter("https".equals(logSearchHttpConfig.getProtocol()),
            new XXssProtectionHeaderWriter(),
            new XFrameOptionsHeaderWriter(XFrameOptionsHeaderWriter.XFrameOptionsMode.DENY),
            new XContentTypeOptionsHeaderWriter(),
            new StaticHeadersWriter("Pragma", "no-cache"),
            new StaticHeadersWriter("Cache-Control", "no-store")))
      .and()
      .csrf().disable()
      .authorizeRequests()
        .requestMatchers(requestMatcher())
          .permitAll()
        .antMatchers("/**")
          .hasRole("USER")
      .and()
      .authenticationProvider(logsearchAuthenticationProvider)
      .httpBasic()
        .authenticationEntryPoint(logsearchAuthenticationEntryPoint())
      .and()
      .addFilterBefore(logsearchTrustedProxyFilter(), BasicAuthenticationFilter.class)
      .addFilterAfter(logsearchKRBAuthenticationFilter(), LogsearchTrustedProxyFilter.class)
      .addFilterBefore(logsearchUsernamePasswordAuthenticationFilter(), LogsearchKRBAuthenticationFilter.class)
      .addFilterAfter(securityContextFormationFilter(), FilterSecurityInterceptor.class)
      .addFilterAfter(logsearchMetadataFilter(), LogsearchSecurityContextFormationFilter.class)
      .addFilterAfter(logsearchAuditLogFilter(), LogsearchSecurityContextFormationFilter.class)
      .addFilterAfter(logsearchServiceLogFilter(), LogsearchSecurityContextFormationFilter.class)
      .addFilterAfter(logSearchConfigStateFilter(), LogsearchSecurityContextFormationFilter.class)
      .addFilterBefore(logsearchCorsFilter(), LogsearchSecurityContextFormationFilter.class)
      .addFilterBefore(logsearchJwtFilter(), LogsearchSecurityContextFormationFilter.class)
      .logout()
        .logoutUrl("/logout")
        .deleteCookies(getCookies())
        .logoutSuccessHandler(new LogsearchLogoutSuccessHandler());

    if ((logSearchConfigApiConfig.isSolrFilterStorage() || logSearchConfigApiConfig.isZkFilterStorage())
            && !logSearchConfigApiConfig.isConfigApiEnabled())
      http.addFilterAfter(logSearchLogLevelFilterManagerFilter(), LogsearchSecurityContextFormationFilter.class);
  }

  @Bean
  public LdapContextSource ldapContextSource() {
    if (authPropsConfig.isAuthLdapEnabled()) {
      final LdapContextSource ldapContextSource = new LdapContextSource();
      ldapContextSource.setUrl(authPropsConfig.getLdapAuthConfig().getLdapUrl());
      ldapContextSource.setBase(authPropsConfig.getLdapAuthConfig().getLdapBaseDn());
      if (StringUtils.isNotBlank(authPropsConfig.getLdapAuthConfig().getLdapManagerDn())) {
        ldapContextSource.setUserDn(authPropsConfig.getLdapAuthConfig().getLdapManagerDn());
      }
      char[] ldapPassword = getLdapManagerPassword();
      if (ldapPassword != null) {
        ldapContextSource.setPassword(new String(ldapPassword));
      }
      ldapContextSource.setReferral(authPropsConfig.getLdapAuthConfig().getReferralMethod());
      ldapContextSource.setAnonymousReadOnly(true);
      ldapContextSource.afterPropertiesSet();
      return ldapContextSource;
    }
    return null;
  }

  @Bean
   public LdapTemplate ldapTemplate() {
     if (authPropsConfig.isAuthLdapEnabled()) {
       return new LdapTemplate(ldapContextSource());
     }

     return null;
   }

  @Bean
  public BindAuthenticator bindAuthenticator() {
    if (authPropsConfig.isAuthLdapEnabled()) {
      final BindAuthenticator bindAuthenticator = new BindAuthenticator(ldapContextSource());
      if (StringUtils.isNotBlank(authPropsConfig.getLdapAuthConfig().getLdapUserDnPattern())) {
        bindAuthenticator.setUserDnPatterns(new String[]{authPropsConfig.getLdapAuthConfig().getLdapUserDnPattern()});
      }
      if (StringUtils.isNotBlank(authPropsConfig.getLdapAuthConfig().getLdapUserSearchFilter())) {
        bindAuthenticator.setUserSearch(new FilterBasedLdapUserSearch(
          authPropsConfig.getLdapAuthConfig().getLdapUserSearchBase(),
          authPropsConfig.getLdapAuthConfig().getLdapUserSearchFilter(),
          ldapContextSource()));
      }

      return bindAuthenticator;
    }
    return null;
  }

  @Bean
  public LdapAuthoritiesPopulator ldapAuthoritiesPopulator() {
    if (authPropsConfig.isAuthLdapEnabled() || StringUtils.isNotBlank(authPropsConfig.getLdapAuthConfig().getLdapGroupSearchBase())) {
      final DefaultLdapAuthoritiesPopulator ldapAuthoritiesPopulator =
        new DefaultLdapAuthoritiesPopulator(ldapContextSource(), authPropsConfig.getLdapAuthConfig().getLdapGroupSearchBase());
      ldapAuthoritiesPopulator.setGroupSearchFilter(authPropsConfig.getLdapAuthConfig().getLdapGroupSearchFilter());
      ldapAuthoritiesPopulator.setGroupRoleAttribute(authPropsConfig.getLdapAuthConfig().getLdapGroupRoleAttribute());
      ldapAuthoritiesPopulator.setSearchSubtree(true);
      ldapAuthoritiesPopulator.setConvertToUpperCase(true);
      return ldapAuthoritiesPopulator;
    }
    return new NullLdapAuthoritiesPopulator();
  }

  @Bean
  public LogsearchLdapAuthenticationProvider ldapAuthenticationProvider() {
    if (authPropsConfig.isAuthLdapEnabled()) {
      LogsearchLdapAuthenticationProvider provider = new LogsearchLdapAuthenticationProvider(bindAuthenticator(), ldapAuthoritiesPopulator());
      provider.setAuthoritiesMapper(new LogSearchLdapAuthorityMapper(authPropsConfig.getLdapAuthConfig().getLdapGroupRoleMap()));
      return provider;
    }
    return null;
  }

  @Bean
  public LogsearchCorsFilter logsearchCorsFilter() {
    return new LogsearchCorsFilter(logSearchHttpHeaderConfig);
  }

  @Bean
  public LogsearchSecurityContextFormationFilter securityContextFormationFilter() {
    return new LogsearchSecurityContextFormationFilter();
  }

  @Bean
  public LogsearchKRBAuthenticationFilter logsearchKRBAuthenticationFilter() {
    return new LogsearchKRBAuthenticationFilter(requestMatcher());
  }

  @Bean
  public LogsearchTrustedProxyFilter logsearchTrustedProxyFilter() throws Exception {
    LogsearchTrustedProxyFilter filter = new LogsearchTrustedProxyFilter(requestMatcher(), authPropsConfig);
    filter.setAuthenticationManager(authenticationManagerBean());
    return filter;
  }

  @Bean
  public LogsearchJWTFilter logsearchJwtFilter() throws Exception {
    LogsearchJWTFilter filter = new LogsearchJWTFilter(requestMatcher(), authPropsConfig, roleDao);
    filter.setAuthenticationManager(authenticationManagerBean());
    filter.setAuthenticationSuccessHandler(new LogsearchAuthSuccessHandler());
    filter.setAuthenticationFailureHandler(new LogsearchAuthFailureHandler());
    return filter;
  }

  @Bean
  public LogsearchAuthenticationEntryPoint logsearchAuthenticationEntryPoint() {
    LogsearchAuthenticationEntryPoint entryPoint = new LogsearchAuthenticationEntryPoint("/login", authPropsConfig);
    entryPoint.setForceHttps(false);
    entryPoint.setUseForward(authPropsConfig.isRedirectForward());
    return entryPoint;
  }

  @Bean
  public LogsearchUsernamePasswordAuthenticationFilter logsearchUsernamePasswordAuthenticationFilter() throws Exception {
    LogsearchUsernamePasswordAuthenticationFilter filter = new LogsearchUsernamePasswordAuthenticationFilter();
    filter.setAuthenticationSuccessHandler(new LogsearchAuthSuccessHandler());
    filter.setAuthenticationFailureHandler(new LogsearchAuthFailureHandler());
    filter.setAuthenticationManager(authenticationManagerBean());
    return filter;
  }

  @Bean
  public PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder();
  }

  private LogsearchFilter logsearchServiceLogFilter() {
    return new LogsearchFilter(serviceLogsRequestMatcher(), new GlobalStateProvider(solrServiceLogsState, solrServiceLogPropsConfig));
  }

  private LogsearchFilter logsearchAuditLogFilter() {
    return new LogsearchFilter(auditLogsRequestMatcher(), new GlobalStateProvider(solrAuditLogsState, solrAuditLogPropsConfig));
  }

  private LogsearchFilter logsearchMetadataFilter() {
    return new LogsearchFilter(metadataRequestMatcher(), new GlobalStateProvider(solrMetadataState, solrEventHistoryPropsConfig));
  }

  private LogsearchFilter logSearchConfigStateFilter() {
    RequestMatcher requestMatcher;
    if (logSearchConfigApiConfig.isSolrFilterStorage() || logSearchConfigApiConfig.isZkFilterStorage()) {
      requestMatcher = shipperConfigInputRequestMatcher();
    } else {
      requestMatcher = logsearchConfigRequestMatcher();
    }

    return new LogsearchFilter(requestMatcher, new ConfigStateProvider(logSearchConfigState, logSearchConfigApiConfig.isConfigApiEnabled()));
  }

  private LogsearchFilter logSearchLogLevelFilterManagerFilter() {
    return new LogsearchFilter(logLevelFilterRequestMatcher(), requestUri ->
            logLevelFilterManagerState.isLogLevelFilterManagerIsReady() ? null : StatusMessage.with(SERVICE_UNAVAILABLE, "Solr log level filter manager is not available"));
  }

  @Bean
  public RequestMatcher requestMatcher() {
    List<RequestMatcher> matchers = Lists.newArrayList();
    matchers.add(new AntPathRequestMatcher("/docs/**"));
    matchers.add(new AntPathRequestMatcher("/swagger-ui/**"));
    matchers.add(new AntPathRequestMatcher("/swagger.html"));
    if (!authPropsConfig.isAuthJwtEnabled()) {
      matchers.add(new AntPathRequestMatcher("/"));
    }
    matchers.add(new AntPathRequestMatcher("/login"));
    matchers.add(new AntPathRequestMatcher("/logout"));
    matchers.add(new AntPathRequestMatcher("/resources/**"));
    matchers.add(new AntPathRequestMatcher("/index.html"));
    matchers.add(new AntPathRequestMatcher("/favicon.ico"));
    matchers.add(new AntPathRequestMatcher("/assets/**"));
    matchers.add(new AntPathRequestMatcher("/templates/**"));
    matchers.add(new AntPathRequestMatcher("/api/v1/info/**"));
    matchers.add(new AntPathRequestMatcher("/api/v1/swagger.json"));
    matchers.add(new AntPathRequestMatcher("/api/v1/swagger.yaml"));
    return new OrRequestMatcher(matchers);
  }

  public RequestMatcher serviceLogsRequestMatcher() {
    return new AntPathRequestMatcher("/api/v1/service/logs/**");
  }

  public RequestMatcher auditLogsRequestMatcher() {
    return new AntPathRequestMatcher("/api/v1/audit/logs/**");
  }

  public RequestMatcher metadataRequestMatcher() {
    return new AntPathRequestMatcher("/api/v1/metadata/**");
  }

  public RequestMatcher logsearchConfigRequestMatcher() {
    return new AntPathRequestMatcher("/api/v1/shipper/**");
  }

  public RequestMatcher logLevelFilterRequestMatcher() {
    return new AntPathRequestMatcher("/api/v1/shipper/filters/**");
  }

  public RequestMatcher shipperConfigInputRequestMatcher() {
    return new AntPathRequestMatcher("/api/v1/shipper/input/**");
  }

  private char[] getLdapManagerPassword() {
    char[] ldapPassword = null;
    try {
      String credentialProviderPath = logSearchSslConfig.getCredentialStoreProviderPath();
      String ldapPasswordEnv = "LOGSEARCH_LDAP_MANAGER_PASSWORD";
      if (StringUtils.isNotBlank(credentialProviderPath)) {
        org.apache.hadoop.conf.Configuration config = new org.apache.hadoop.conf.Configuration();
        config.set(LogSearchSslConfig.CREDENTIAL_STORE_PROVIDER_PATH, credentialProviderPath);
        ldapPassword = config.getPassword("logsearch.auth.ldap.manager.password");
      } else if (StringUtils.isNotBlank(authPropsConfig.getLdapAuthConfig().getLdapManagerPasswordFile())){
        ldapPassword = FileUtils.readFileToString(new File(
          authPropsConfig.getLdapAuthConfig().getLdapManagerPasswordFile()), Charset.defaultCharset()).toCharArray();
      } else if (StringUtils.isNotBlank(System.getenv(ldapPasswordEnv))) {
        ldapPassword = System.getenv(ldapPasswordEnv).toCharArray();
      } else if (StringUtils.isNotBlank(authPropsConfig.getLdapAuthConfig().getLdapManagerPassword())) {
        ldapPassword = authPropsConfig.getLdapAuthConfig().getLdapManagerPassword().toCharArray();
      }
    } catch (Exception e) {
      logger.warn("Error during ldap password initialization. LDAP authentication probably won't work if a manager password will be required.", e);
    }
    return ldapPassword;
  }

  private String[] getCookies() {
    List<String> cookies = new ArrayList<>();
    cookies.add(LOGSEARCH_SESSION_ID);
    if (authPropsConfig.isAuthJwtEnabled()) {
      cookies.add(authPropsConfig.getCookieName());
    }
    return cookies.toArray(new String[0]);
  }

  class LogSearchCompositeHeaderWriter implements HeaderWriter {

    private final boolean sslEnabled;
    private final HeaderWriter[] additionalHeaderWriters;
    private final HstsHeaderWriter hstsHeaderWriter;

    LogSearchCompositeHeaderWriter(boolean sslEnabled, HeaderWriter... additionalHeaderWriters) {
      this.sslEnabled = sslEnabled;
      this.additionalHeaderWriters = additionalHeaderWriters;
      this.hstsHeaderWriter = new HstsHeaderWriter();
    }

    @Override
    public void writeHeaders(HttpServletRequest httpServletRequest, HttpServletResponse httpServletResponse) {
      for (HeaderWriter headerWriter : additionalHeaderWriters) {
        headerWriter.writeHeaders(httpServletRequest, httpServletResponse);
      }
      if (sslEnabled) {
        hstsHeaderWriter.writeHeaders(httpServletRequest, httpServletResponse);
      }
    }
  }

}
