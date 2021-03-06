# Copyright 2012-2013 Isotoma Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from buildbot import config
from buildbot.www.authz import Forbidden
from klein import Klein
from twisted.internet import defer
from twisted.internet import threads
import yaml
import json


def getDbConfigObjectId(master, name="config"):
    return master.db.state.getObjectId(name, "DbConfig")


class Api(object):
    app = Klein()
    _yamlPath = None
    _useDbConfig = False
    _in_progress = False

    def __init__(self, ep):
        self.ep = ep
        self._cfg = None

    def setYamlPath(self, path):
        self._yamlPath = path

    def useDbConfig(self):
        self._useDbConfig = True

    def setCfg(self, cfg):
        self._cfg = cfg
        self._in_progress = False

    @defer.inlineCallbacks
    def saveCfg(self, cfg):
        if self._yamlPath is not None:
            cfg = yaml.safe_dump(cfg, default_flow_style=False, indent=4)
            with open(self._yamlPath, "w") as f:
                f.write(cfg)

        if self._useDbConfig:
            oid = yield getDbConfigObjectId(self.ep.master)
            yield self.ep.master.db.state.setState(oid, "travis", cfg)

        ret = None
        master = self.ep.master
        try:
            yield threads.deferToThreadPool(
                master.reactor, master.reactor.getThreadPool(),
                master.config_loader.loadConfig)

        except config.ConfigErrors as e:
            ret = []
            for msg in e.errors:
                ret.append(msg)
        defer.returnValue(ret)

    @defer.inlineCallbacks
    def assertAllowed(self, request):
        try:
            yield self.ep.master.www.assertUserAllowed(request, tuple(request.path.strip(b"/").split(b"/")),
                                                       request.method, {})
        except Forbidden:
            request.setResponseCode(401)
            defer.returnValue("FORBIDDEN")

    @app.route("/config", methods=['GET'])
    @defer.inlineCallbacks
    def getConfig(self, request):
        res = yield self.assertAllowed(request)
        if res:
            defer.returnValue(res)
        request.setHeader('Content-Type', 'application/json')
        defer.returnValue(json.dumps(self._cfg))

    @app.route("/config", methods=['PUT'])
    @defer.inlineCallbacks
    def saveConfig(self, request):
        """I save the config, and run check_config, potencially returning errors"""
        res = yield self.assertAllowed(request)
        if res:
            defer.returnValue(res)
        request.setHeader('Content-Type', 'application/json')
        if self._in_progress:
            defer.returnValue(json.dumps({'success': False, 'errors': ['reconfig already in progress']}))
        self._in_progress = True
        cfg = json.loads(request.content.read())
        if cfg != self._cfg:
            try:
                err = yield self.saveCfg(cfg)
            except Exception as e:  # noqa
                err = [repr(e)]
            if err is not None:
                self._in_progress = False
                yield self.saveCfg(self._cfg)
                defer.returnValue(json.dumps({'success': False, 'errors': err}))

        yield self.ep.master.reconfig()
        defer.returnValue(json.dumps({'success': True}))
