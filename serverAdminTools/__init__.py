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
import os
import glob
import shutil
import arcpy
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
        desc = arcpy.Describe(ws)
        if desc.workspaceFactoryProgID == 'esriDataSourcesGDB.SdeWorkspaceFactory.1':
            cp = desc.connectionProperties
            props =  ['server', 'instance', 'database', 'version', 'authentication_mode']
            db_client = cp.instance.split(':')[1]
            con_properties = cp.server
            parts = []
            for prop in props:
                parts.append('{}={}'.format(prop.upper(), getattr(cp, prop)))
            parts.insert(2, 'DBCLIENT={}'.format(db_client))
            parts.insert(3, 'DB_CONNECTION_PROPERTIES={}'.format(cp.server))
            return ';'.join(parts)
        else:
            return 'DATABASE=' + ws

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

        for fc in fcs:
            service_map['feature_classes'][fc.split('.')[-1]] = []

        # iterate through services and find matching workspace/layers
        for service in self.ags.iter_services():
            if hasattr(service, 'type') and service.type == 'MapServer':
                # feature servers have map servers too
                manifest = service.manifest()
                if hasattr(manifest, 'databases'):
                    for db in manifest.databases:

                        # iterate through all layers to find workspaces/fc's
                        if con_str in [db.onServerConnectionString, db.onPremiseConnectionString]:
                            service_map['workspace'].append({
                                'name': service.serviceName,
                                'serviceObj': service
                            })
                            if service not in toStop:
                                toStop.append(service)

                            # check for specific feature classes
                            for ds in db.datasets:
                                lyr_name = ds.onServerName
                                if lyr_name in service_map['feature_classes']:
                                    service_map['feature_classes'][lyr_name].append({
                                        'name': service.serviceName,
                                        'serviceObj': service
                                    })
                                    if service not in toStop:
                                        toStop.append(service)

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
        workspaces = []
        manifest = service.manifest()
        if hasattr(manifest, 'databases'):

            for db in manifest.databases:
                # read layer xmls to find all workspaces
                dbType = db.onServerWorkspaceFactoryProgID
                if dbType == 'esriDataSourcesGDB.SdeWorkspaceFactory.1':
                    cs = db.onServerConnectionString or db.onPremiseConnectionString
                    db_name = {k:v for k, v in iter(s.split('=') for s in cs.split(';'))}['DATABASE']
                    sde = os.path.join(sde_loc, db_name + '.sde')
                    workspaces.append(sde)

        if workspaces:

            # stop service
            service.stop()
            self.__stopped_services.append(service)
            Message('Stopped Service...\n')

            # compress databases
            for ws in workspaces:
                arcpy.management.Compress(ws)

            # start service
            service.start()
            self.__started_services.append(service)
            Message('\nStarted Service')

        return workspaces
