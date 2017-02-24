#-------------------------------------------------------------------------------
# Name:        module1
# Purpose:
#
# Author:      calebma
#
# Created:     24/02/2017
# Copyright:   (c) calebma 2017
# Licence:     <your licence>
#-------------------------------------------------------------------------------
from restapi import admin
from _xmlBase import *
import os
import glob
import shutil
import sys
if sys.version_info[0] == 3:
    from tempfile import TemporaryDirectory
else:
    import tempfile
    class TemporaryDirectory(object):
        """Context manager for tempfile.mkdtemp() so it's usable with "with" statement."""
        def __enter__(self):
            self.name = tempfile.mkdtemp()
            return self.name

        def __exit__(self, exc_type, exc_value, traceback):
            try:
                shutil.rmtree(self.name)
            except:
                try:
                    os.rmdir(self.name)
                except:pass

import arcpy
from restapi import admi
from restapi.rest_utils import namedTuple

__all__ = ['ServerAdministrator']

def Message(*args):
    """Prints message to Script tool window or python shell

    msg: message to be printed
    """
    if isinstance(args, (list, tuple)):
        for msg in args:
            print str(msg)
            arcpy.AddMessage(str(msg))
    else:
        print str(msg)
        arcpy.AddMessage(str(msg))

class ServerAdministrator(object):
    def __init__(self, server_url, usr='', pw='', token=''):
        self.ags = admin.ArcServerAdmin(server_url, usr, pw, token)
        self.__stopped_services = []
        self.__started_services = []

    @staticmethod
    def find_ws(path, ws_type='', return_type=False):
        """finds a valid workspace path for an arcpy.da.Editor() Session

        Required:
            path -- path to features or workspace

        Optional:
            ws_type -- option to find specific workspace type (FileSystem|LocalDatabase|RemoteDatabase)
            return_type -- option to return workspace type as well.  If this option is selected, a tuple
                of the full workspace path and type are returned

        """
        def find_existing(path):
            if arcpy.Exists(path):
                return path
            else:
                if not arcpy.Exists(path):
                    return find_existing(os.path.dirname(path))

        # try original path first
        if isinstance(path, (arcpy.mapping.Layer, arcpy.mapping.TableView)):
            path = path.dataSource
        if os.sep not in str(path):
            if hasattr(path, 'dataSource'):
                path = path.dataSource
            else:
                path = arcpy.Describe(path).catalogPath

        path = find_existing(path)
        desc = arcpy.Describe(path)
        if hasattr(desc, 'workspaceType'):
            if ws_type == desc.workspaceType:
                if return_type:
                    return (path, desc.workspaceType)
                else:
                    return path
            else:
                if return_type:
                    return (path, desc.workspaceType)
                else:
                    return path

        # search until finding a valid workspace
        path = str(path)
        split = filter(None, str(path).split(os.sep))
        if path.startswith('\\\\'):
            split[0] = r'\\{0}'.format(split[0])

        # find valid workspace
        for i in xrange(1, len(split)):
            sub_dir = os.sep.join(split[:-i])
            desc = arcpy.Describe(sub_dir)
            if hasattr(desc, 'workspaceType'):
                if ws_type == desc.workspaceType:
                    if return_type:
                        return (sub_dir, desc.workspaceType)
                    else:
                        return sub_dir
                else:
                    if return_type:
                        return (sub_dir, desc.workspaceType)
                    else:
                        return sub_dir

    @staticmethod
    def form_connection_string(ws):
        """esri's describe workspace connection string does not work at 10.4, bug???"""
        cp = arcpy.Describe(ws).connectionProperties
        props =  ['server', 'instance', 'database', 'version', 'authentication_mode']
        db_client = cp.instance.split(':')[1]
        con_properties = cp.server
        parts = []
        for prop in props:
            parts.append('{}={}'.format(prop.upper(), getattr(cp, prop)))
        parts.insert(2, 'DBCLIENT={}'.format(db_client))
        parts.insert(3, 'DB_CONNECTION_PROPERTIES={}'.format(cp.server))
        return ';'.join(parts)

    def find_services_containing(self, ws, fcs=[], stop=False):
        """finds services containing an entire workspace and any specific feature classes

        Required:
            ws -- SDE workspace path
            fcs -- list of specific feature classes to search for

        Optional:
            stop -- option to stop service once item is found
        """
        ws = self.find_ws(ws)
        con_str = self.form_connection_string(ws)
        service_map = {'workspace': [], 'feature_classes': {}}
        toStop = []
        with TemporaryDirectory() as tmp:
            for fc in fcs:
                service_map['feature_classes'][fc] = []

            # iterate through services and find matching workspace/layers
            for service in self.ags.iter_services():
                if hasattr(service, 'type') and service.type == 'MapServer':
                    ws_found = False
                    layers_found = []
                    # feature servers have map servers too
                    msd = service.properties.filePath
                    if os.path.exists(msd):
                        tmp = tempfile.mkdtemp()
                        unzip(msd, tmp)

                        # iterate through all layers to find workspaces/fc's
                        layer_path = os.path.join(tmp, 'layers')
                        for fl in glob.glob(os.path.join(layer_path, '*.xml')):
                            if os.path.basename(fl) != 'layers.xml':
                                doc = BaseXML(fl)
                                # get connection string from xml
                                csElm = doc.getElm('WorkspaceConnectionString')
                                if csElm is not None:
                                    cs = csElm.text
                                    if cs == con_str and not ws_found:
                                        service_map['workspace'].append({
                                            'name': service.serviceName,
                                            'serviceObj': service
                                        })
                                        ws_found = True
                                        if service not in toStop:
                                            toStop.append(service)


                                    # check for specific feature classes
                                    lyrElm = doc.getElm('Dataset')
                                    if lyrElm is not None and fcs:
                                        lyr_name = lyrElm.text
                                        if lyr_name in service_map['feature_classes']:
                                            service_map['feature_classes'][lyr_name].append({
                                                'name': service.serviceName,
                                                'serviceObj': service
                                            })
                                            layers_found.append(lyr_name)
                                            if service not in toStop:
                                                toStop.append(service)


                            # clean up
                            try:
                                os.remove(fl)
                            except: pass

                            # stop searching in this service if workspace and/or all layers have been found
                            if ws_found and (not fcs or sorted(fcs) == sorted(layers_found)):
                                break


        if stop:
            for service in toStop:
                service.stop()
                Message('Stopped service: "{}"'.format(service.serviceName))
                self.__stopped_services.append(service)
        return service_map


    def startStoppedServices(self):
        """start all stopped services that are in this instances cache, meaning those
        that have been stopped from this instance
        """
        for s in self.__stopped_services:
            s.start()
            Message('Started service: "{}"'.format(s.serviceName))
            self.__stopped_services.remove(s)
            self.__started_services.append(s)


    def stopServiceAndCompressDatabase(self, sde_loc, service_url_or_name):
        """will stop a service and compress all SDE databases within the map service

        Required:
            sde_loc -- location containing .sde connections
            service_url_or_name -- full path to REST endpoint or service name
        """
        service = self.ags.service(service_url_or_name)
        msd = service.properties.filePath
        workspaces = {}
        with TemporaryDirectory() as tmp:
            unzip(msd, tmp)

            # read layer xmls to find all workspaces
            layer_path = os.path.join(tmp, 'layers')
            for fl in glob.glob(os.path.join(layer_path, '*.xml')):
                doc = BaseXML(fl)
                for node in doc.iterTags('DataConnection'):
                    wsd = {c.tag: c.text for c in iter(node)}
                    ws = namedTuple('DataConnection', wsd)
                    if ws.WorkspaceFactory == 'SDE' and ws.Dataset.split('.')[0] not in workspaces:
                        workspaces[ws.Dataset.split('.')[0]] = ws

            # stop service
            service.stop()
            self.__stopped_services.append(service)
            Message('Stopped Service...\n')

            # compress sde
            for db_name in workspaces.keys():
                sde = os.path.join(sde_loc, db_name + '.sde')
                arcpy.management.Compress(sde)
                Message('Compressed SDE Database: {}'.format(os.path.basename(sde)))

            # start service
            service.start()
            self.__started_services.append(service)
            Message('\nStarted Service')

        return workspaces
