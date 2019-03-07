# -*- coding: utf-8 -*-
import os
import logging
import posixpath

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from seahub.api2.authentication import TokenAuthentication
from seahub.api2.throttling import UserRateThrottle
from seahub.api2.utils import api_error
from seahub.related_files.models import RelatedFiles
from seahub.tags.models import FileUUIDMap
from seahub.utils import normalize_file_path
from seahub.utils.timeutils import timestamp_to_isoformat_timestr
from seahub.views import check_folder_permission
from seahub.constants import PERMISSION_READ_WRITE

from seaserv import seafile_api


logger = logging.getLogger(__name__)


class RelatedFilesView(APIView):

    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated,)
    throttle_classes = (UserRateThrottle,)

    def get_related_file(self, repo_id, file_name, file_path):
        related_file = dict()
        related_file["name"] = file_name
        related_file["repo_id"] = repo_id
        r_repo = seafile_api.get_repo(repo_id)
        if not r_repo:
            related_file["repo_name"] = ""
        else:
            related_file["repo_name"] = r_repo.name
        related_file["path"] = file_path
        file_obj = seafile_api.get_dirent_by_path(repo_id, file_path)
        related_file["size"] = file_obj.size
        related_file["last_modified"] = timestamp_to_isoformat_timestr(file_obj.mtime)
        return related_file

    def get(self, request):
        """list all related files of a file.
        """
        # argument check
        repo_id = request.GET.get('repo_id')
        if not repo_id:
            error_msg = 'repo_id invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)
        
        file_path = request.GET.get('file_path')
        if not file_path:
            error_msg = 'file_path invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)
        file_path = normalize_file_path(file_path)

        # resource check
        repo = seafile_api.get_repo(repo_id)
        if not repo:
            error_msg = 'Library %s not found.' % repo_id
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)

        file_id = seafile_api.get_file_id_by_path(repo_id, file_path)
        if not file_id:
            error_msg = 'File %s not found.' % file_path
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)

        # permission check
        if not check_folder_permission(request, repo_id, '/'):
            error_msg = 'Permission denied.'
            return api_error(status.HTTP_403_FORBIDDEN, error_msg)

        filename = os.path.basename(file_path)
        parent_path = os.path.dirname(file_path)
        uuid = FileUUIDMap.objects.get_or_create_fileuuidmap(repo_id, parent_path, filename, is_dir=False)
        uuid = str(uuid.uuid).replace('-', '')
        try:
            related_file_list = RelatedFiles.objects.get_related_files(uuid)
        except Exception as e:
            logger.error(e)
            error_msg = 'Internal Server Error.'
            return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)

        related_files = list()
        for file_obj in related_file_list:
            related_id = file_obj[0]
            o_uuid = file_obj[1]
            r_uuid = file_obj[2]
            o_repo_id = file_obj[3]
            o_parent_path = file_obj[4]
            o_file_name = file_obj[5]
            r_repo_id = file_obj[6]
            r_parent_path = file_obj[7]
            r_file_name = file_obj[8]
            if o_uuid == uuid:
                r_path = posixpath.join(r_parent_path, r_file_name)
                r_file_id = seafile_api.get_file_id_by_path(r_repo_id, r_path)
                if not r_file_id:
                    try:
                        RelatedFiles.objects.delete_related_file_uuid(related_id)
                    except Exception as e:
                        logger.error(e)
                        error_msg = 'Internal Server Error.'
                        return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)
                    continue
                related_file = self.get_related_file(r_repo_id, r_file_name, r_path)
                related_file["related_id"] = related_id
                related_files.append(related_file)
            if r_uuid == uuid:
                o_path = posixpath.join(o_parent_path, o_file_name)
                o_file_id = seafile_api.get_file_id_by_path(r_repo_id, o_path)
                if not o_file_id:
                    try:
                        RelatedFiles.objects.delete_related_file_uuid(related_id)
                    except Exception as e:
                        logger.error(e)
                        error_msg = 'Internal Server Error.'
                        return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)
                    continue
                related_file = self.get_related_file(o_repo_id, o_file_name, o_path)
                related_file["related_id"] = related_id
                related_files.append(related_file)

        return Response({"related_files": related_files}, status=status.HTTP_200_OK)

    def post(self, request):
        """add a related file for a file
        """
        # argument check
        o_repo_id = request.data.get('o_repo_id')
        if not o_repo_id:
            error_msg = 'o_repo_id invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

        r_repo_id = request.data.get('r_repo_id')
        if not r_repo_id:
            error_msg = 'r_repo_id invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

        o_path = request.data.get('o_path')
        if not o_path:
            error_msg = 'o_file_path invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)
        o_path = normalize_file_path(o_path)

        r_path = request.data.get('r_path')
        if not r_path:
            error_msg = 'r_file_path invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)
        r_path = normalize_file_path(r_path)

        if o_repo_id == r_repo_id and o_path == r_path:
            error_msg = 'Cannot relate itself.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

        # resource check
        o_repo = seafile_api.get_repo(o_repo_id)
        r_repo = seafile_api.get_repo(r_repo_id)
        if not o_repo:
            error_msg = 'Library %s not found.' % o_repo_id
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)
        if not r_repo:
            error_msg = 'Library %s not found.' % r_repo_id
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)

        o_file_id = seafile_api.get_file_id_by_path(o_repo_id, o_path)
        r_file_id = seafile_api.get_file_id_by_path(r_repo_id, r_path)
        if not o_file_id:
            error_msg = 'File %s not found.' % o_path
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)
        if not r_file_id:
            error_msg = 'File %s not found.' % r_path
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)

        related_file_uuid = RelatedFiles.objects.get_related_file_uuid(o_repo_id, r_repo_id, o_path, r_path)
        if related_file_uuid:
            error_msg = 'related file already exist.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

        # permission check
        if check_folder_permission(request, o_repo_id, '/') != PERMISSION_READ_WRITE:
            error_msg = 'Permission denied.'
            return api_error(status.HTTP_403_FORBIDDEN, error_msg)

        try:
            related_file_uuid = RelatedFiles.objects.add_related_file_uuid(o_repo_id, r_repo_id, o_path, r_path)
        except Exception as e:
            logger.error(e)
            error_msg = 'Internal Server Error.'
            return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)

        r_file_name = os.path.basename(r_path)
        related_file = self.get_related_file(r_repo_id, r_file_name, r_path)
        related_file["related_id"] = related_file_uuid.pk

        return Response({"related_file": related_file}, status.HTTP_201_CREATED)


class RelatedFileView(APIView):

    authentication_classes = (TokenAuthentication, SessionAuthentication)
    permission_classes = (IsAuthenticated,)
    throttle_classes = (UserRateThrottle,)

    def delete(self, request, related_id):
        """delete a related file from a file
        """
        # argument check
        repo_id = request.data.get('repo_id')
        if not repo_id:
            error_msg = 'repo_id invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)

        file_path = request.data.get('file_path')
        if not file_path:
            error_msg = 'file_path invalid.'
            return api_error(status.HTTP_400_BAD_REQUEST, error_msg)
        file_path = normalize_file_path(file_path)

        # resource check
        related_file_uuid = RelatedFiles.objects.get_related_file_uuid_by_id(related_id)
        if not related_file_uuid:
            error_msg = 'Related %s does not exist.' % related_id
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)

        file_id = seafile_api.get_file_id_by_path(repo_id, file_path)
        if not file_id:
            error_msg = 'File %s not found.' % file_path
            return api_error(status.HTTP_404_NOT_FOUND, error_msg)

        # permission check
        if check_folder_permission(request, repo_id, '/') != PERMISSION_READ_WRITE:
            error_msg = 'Permission denied.'
            return api_error(status.HTTP_403_FORBIDDEN, error_msg)

        try:
            RelatedFiles.objects.delete_related_file_uuid(related_id)
        except Exception as e:
            logger.error(e)
            error_msg = 'Internal Server Error.'
            return api_error(status.HTTP_500_INTERNAL_SERVER_ERROR, error_msg)

        return Response({"success": "true"}, status=status.HTTP_200_OK)
