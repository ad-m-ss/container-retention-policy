import asyncio
from asyncio import Semaphore
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import partial
from unittest.mock import AsyncMock, Mock

import pytest as pytest
from httpx import AsyncClient
from pydantic import ValidationError

import main
from main import (
    AccountType,
    ImageName,
    Inputs,
    delete_org_package_versions,
    delete_package_versions,
    get_and_delete_old_versions,
    list_org_package_versions,
    list_package_versions,
)
from main import main as main_
from main import post_deletion_output

mock_response = Mock()
mock_response.json.return_value = []
mock_response.is_error = False
mock_bad_response = Mock()
mock_bad_response.is_error = True
mock_http_client = AsyncMock()
mock_http_client.get.return_value = mock_response
mock_http_client.delete.return_value = mock_response


@pytest.mark.asyncio
async def test_list_org_package_version():
    await list_org_package_versions(org_name='test', image_name=ImageName('test', 'test'), http_client=mock_http_client)


@pytest.mark.asyncio
async def test_list_package_version():
    await list_package_versions(image_name=ImageName('test', 'test'), http_client=mock_http_client)


@pytest.mark.asyncio
async def test_delete_org_package_version():
    await delete_org_package_versions(
        org_name='test',
        image_name=ImageName('test', 'test'),
        http_client=mock_http_client,
        version_id=123,
        semaphore=Semaphore(1),
    )


@pytest.mark.asyncio
async def test_delete_package_version():
    await delete_package_versions(
        image_name=ImageName('test', 'test'), http_client=mock_http_client, version_id=123, semaphore=Semaphore(1)
    )


@pytest.mark.asyncio
async def test_delete_package_version_semaphore():
    """
    Bit of a useless test, but proves Semaphores work the way we think.
    """
    # Test that we're still waiting after 1 second, when the semaphore is empty
    sem = Semaphore(0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            delete_package_versions(
                image_name=ImageName('test', 'test'), http_client=mock_http_client, version_id=123, semaphore=sem
            ),
            1,
        )

    # Assert that this would not be the case otherwise
    sem = Semaphore(1)
    await asyncio.wait_for(
        delete_package_versions(
            image_name=ImageName('test', 'test'), http_client=mock_http_client, version_id=123, semaphore=sem
        ),
        1,
    )


def test_post_deletion_output(capsys):
    # Happy path
    post_deletion_output(response=mock_response, image_name=ImageName('test', 'test'), version_id=123)
    captured = capsys.readouterr()
    assert captured.out == 'Deleted old image: test:123\n'

    # Bad response
    post_deletion_output(response=mock_bad_response, image_name=ImageName('test', 'test'), version_id=123)
    captured = capsys.readouterr()
    assert captured.out != 'Deleted old image: test:123\n'


input_defaults = {
    'image_names': 'a,b',
    'cut_off': 'an hour ago utc',
    'timestamp_to_use': 'created_at',
    'untagged_only': 'false',
    'skip_tags': '',
    'keep_at_least': '0',
    'filter_tags': '',
    'filter_include_untagged': 'true',
    'token': 'test',
    'account_type': 'personal',
}


def _create_inputs_model(**kwargs):
    """
    Little helper method, to help us instantiate working Inputs models.
    """

    return Inputs(**(input_defaults | kwargs))


def test_org_name_empty():
    with pytest.raises(ValidationError):
        Inputs(**(input_defaults | {'account_type': 'org', 'org_name': ''}))


@pytest.mark.asyncio
async def test_inputs_model_personal(mocker):
    # Mock the personal list function
    mocked_list_package_versions: AsyncMock = mocker.patch.object(main, 'list_package_versions', AsyncMock())
    mocked_delete_package_versions: AsyncMock = mocker.patch.object(main, 'delete_package_versions', AsyncMock())

    # Create a personal inputs model
    personal = _create_inputs_model(account_type='personal')
    assert (personal.account_type == AccountType.ORG) is False

    # Call the GithubAPI utility function
    await main.GithubAPI.list_package_versions(
        account_type=personal.account_type,
        org_name=personal.org_name,
        image_name=personal.image_names[0],
        http_client=AsyncMock(),
    )
    await main.GithubAPI.delete_package(
        account_type=personal.account_type,
        org_name=personal.org_name,
        image_name=personal.image_names[0],
        http_client=AsyncMock(),
        version_id=1,
        semaphore=Semaphore(1),
    )

    # Make sure the right function was called
    mocked_list_package_versions.assert_awaited_once()
    mocked_delete_package_versions.assert_awaited_once()


@pytest.mark.asyncio
async def test_inputs_model_org(mocker):
    # Mock the org list function
    mocked_list_package_versions: AsyncMock = mocker.patch.object(main, 'list_org_package_versions', AsyncMock())
    mocked_delete_package_versions: AsyncMock = mocker.patch.object(main, 'delete_org_package_versions', AsyncMock())

    # Create a personal inputs model
    org = _create_inputs_model(account_type='org', org_name='test')
    assert (org.account_type == AccountType.ORG) is True

    # Call the GithubAPI utility function
    await main.GithubAPI.list_package_versions(
        account_type=org.account_type, org_name=org.org_name, image_name=org.image_names[0], http_client=AsyncMock()
    )
    await main.GithubAPI.delete_package(
        account_type=org.account_type,
        org_name=org.org_name,
        image_name=org.image_names[0],
        http_client=AsyncMock(),
        version_id=1,
        semaphore=Semaphore(1),
    )

    # Make sure the right function was called
    mocked_list_package_versions.assert_awaited_once()
    mocked_delete_package_versions.assert_awaited_once()


class TestGetAndDeleteOldVersions:
    valid_data = [
        {
            'created_at': '2021-05-26T14:03:03Z',
            'html_url': 'https://github.com/orgs/org-name/packages/container/image-name/1234567',
            'id': 1234567,
            'metadata': {'container': {'tags': []}, 'package_type': 'container'},
            'name': 'sha256:3c6891187412bd31fa04c63b4f06c47417eb599b1b659462632285531aa99c19',
            'package_html_url': 'https://github.com/orgs/org-name/packages/container/package/image-name',
            'updated_at': '2021-05-26T14:03:03Z',
            'url': 'https://api.github.com/orgs/org-name/packages/container/image-name/versions/1234567',
        }
    ]

    @staticmethod
    async def _mock_list_package_versions(data, *args, **kwargs):
        """
        This isn't trying to match the signature of the code we're mocking.

        Rather, we're just hacking this together, to return the data we want.
        """
        return data

    @pytest.mark.asyncio
    async def test_delete_package(self, mocker, capsys):
        mocker.patch.object(
            main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, self.valid_data)
        )
        inputs = _create_inputs_model()
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'Deleted old image: a:1234567\n'

    @pytest.mark.asyncio
    async def test_keep_at_least(self, mocker, capsys):
        mocker.patch.object(
            main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, self.valid_data)
        )
        inputs = _create_inputs_model(keep_at_least=1)
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'No more versions to delete for a\n'

    @pytest.mark.asyncio
    async def test_not_beyond_cutoff(self, mocker, capsys):
        response_data = [
            {
                'created_at': str(datetime.now(timezone(timedelta(hours=1)))),
                'id': 1234567,
            }
        ]
        mocker.patch.object(
            main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, response_data)
        )
        inputs = _create_inputs_model()
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'No more versions to delete for a\n'

    @pytest.mark.asyncio
    async def test_missing_timestamp(self, mocker, capsys):
        data = [{'created_at': '', 'id': 1234567}]
        mocker.patch.object(main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, data))
        inputs = _create_inputs_model()
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert (
            captured.out
            == 'Skipping image version 1234567. Unable to parse timestamps.\nNo more versions to delete for a\n'
        )

    @pytest.mark.asyncio
    async def test_empty_list(self, mocker, capsys):
        data = []
        mocker.patch.object(main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, data))
        inputs = _create_inputs_model()
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'No more versions to delete for a\n'

    @pytest.mark.asyncio
    async def test_skip_tags(self, mocker, capsys):
        data = deepcopy(self.valid_data)
        data[0]['metadata'] = {'container': {'tags': ['abc', 'bcd']}}
        mocker.patch.object(main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, data))
        inputs = _create_inputs_model(skip_tags='abc')
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'No more versions to delete for a\n'

    @pytest.mark.asyncio
    async def test_skip_tags_wildcard(self, mocker, capsys):
        data = deepcopy(self.valid_data)
        data[0]['metadata'] = {'container': {'tags': ['v1.0.0', 'abc']}}
        mocker.patch.object(main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, data))
        inputs = _create_inputs_model(skip_tags='v*')
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'No more versions to delete for a\n'

    @pytest.mark.asyncio
    async def test_untagged_only(self, mocker, capsys):
        data = deepcopy(self.valid_data)
        data[0]['metadata'] = {'container': {'tags': ['abc', 'bcd']}}
        mocker.patch.object(main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, data))
        inputs = _create_inputs_model(untagged_only='true')
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'No more versions to delete for a\n'

    @pytest.mark.asyncio
    async def test_filter_tags(self, mocker, capsys):
        data = deepcopy(self.valid_data)
        data[0]['metadata'] = {'container': {'tags': ['sha-deadbeef', 'edge']}}
        mocker.patch.object(main.GithubAPI, 'list_package_versions', partial(self._mock_list_package_versions, data))
        inputs = _create_inputs_model(filter_tags='sha-*')
        await get_and_delete_old_versions(image_name=ImageName('a', 'a'), inputs=inputs, http_client=mock_http_client)
        captured = capsys.readouterr()
        assert captured.out == 'Deleted old image: a:1234567\n'


def test_inputs_bad_account_type():
    # Account type
    _create_inputs_model(account_type='personal')
    _create_inputs_model(account_type='org')
    with pytest.raises(ValidationError, match='is not a valid enumeration member'):
        _create_inputs_model(account_type='')

    # Org name
    _create_inputs_model(org_name='', account_type='personal')
    with pytest.raises(ValueError, match='org-name is required when account-type is org'):
        _create_inputs_model(org_name='', account_type='org')

    # Timestamp type
    _create_inputs_model(timestamp_to_use='updated_at')
    _create_inputs_model(timestamp_to_use='created_at')
    with pytest.raises(ValueError, match=' value is not a valid enumeration mem'):
        _create_inputs_model(timestamp_to_use='wat')

    # Cut-off
    _create_inputs_model(cut_off='21 July 2013 10:15 pm +0500')
    _create_inputs_model(cut_off='12/12/12 PM EST')
    with pytest.raises(ValueError, match='Timezone is required for the cut-off'):
        _create_inputs_model(cut_off='12/12/12')
    with pytest.raises(ValueError, match="Unable to parse 'lolol'"):
        _create_inputs_model(cut_off='lolol')

    # Untagged only
    for i in ['true', 'True', '1']:
        assert _create_inputs_model(untagged_only=i).untagged_only is True
    for j in ['False', 'false', '0']:
        assert _create_inputs_model(untagged_only=j).untagged_only is False
    assert _create_inputs_model(untagged_only=False).untagged_only is False

    # Skip tags
    assert _create_inputs_model(skip_tags='a').skip_tags == ['a']
    assert _create_inputs_model(skip_tags='a,b').skip_tags == ['a', 'b']
    assert _create_inputs_model(skip_tags='a , b  ,c').skip_tags == ['a', 'b', 'c']

    # Keep at least
    with pytest.raises(ValueError, match='ensure this value is greater than or equal to 0'):
        _create_inputs_model(keep_at_least='-1')

    # Filter tags
    assert _create_inputs_model(filter_tags='a').filter_tags == ['a']
    assert _create_inputs_model(filter_tags='sha-*,latest').filter_tags == ['sha-*', 'latest']
    assert _create_inputs_model(filter_tags='sha-* , latest').filter_tags == ['sha-*', 'latest']

    # Filter include untagged
    for i in ['true', 'True', '1', True]:
        assert _create_inputs_model(filter_include_untagged=i).filter_include_untagged is True
    for j in ['False', 'false', '0', False]:
        assert _create_inputs_model(filter_include_untagged=j).filter_include_untagged is False


def test_parse_image_names():
    assert _create_inputs_model(image_names='a').image_names == [ImageName('a', 'a')]
    assert _create_inputs_model(image_names='a,b').image_names == [ImageName('a', 'a'), ImageName('b', 'b')]
    assert _create_inputs_model(image_names='  a  ,  b ').image_names == [ImageName('a', 'a'), ImageName('b', 'b')]
    assert _create_inputs_model(image_names='a/a').image_names == [ImageName('a/a', 'a%2Fa')]


@pytest.mark.asyncio
async def test_main(mocker):
    mocker.patch.object(AsyncClient, 'get', return_value=mock_response)
    mocker.patch.object(AsyncClient, 'delete', return_value=mock_response)
    mocker.patch.object(main, 'get_and_delete_old_versions', AsyncMock())
    await main_(
        **{
            'account_type': 'org',
            'org_name': 'test',
            'image_names': 'a,b,c',
            'timestamp_to_use': 'updated_at',
            'cut_off': '2 hours ago UTC',
            'untagged_only': 'false',
            'skip_tags': '',
            'keep_at_least': '0',
            'filter_tags': '',
            'filter_include_untagged': 'true',
            'token': 'test',
        }
    )


@pytest.mark.asyncio
async def test_public_images_with_more_than_5000_downloads(mocker, capsys):
    """
    The `response.is_error` block is set up to output errors when we run into them.

    One more commonly seen error is the case where an image is public and has more than 5000 downloads.

    For these cases, instead of just outputting the error, we bundle the images names and list
    them once at the end, with the necessary context to act on them if wanted.
    """
    mock_delete_response = Mock()
    mock_delete_response.is_error = True
    mock_delete_response.status_code = 400
    mock_delete_response.json = lambda: {'message': main.PUBLIC_IMAGE_MSG}

    mock_list_response = Mock()
    mock_list_response.is_error = True
    mock_list_response.status_code = 400
    mock_list_response.json = lambda: [{'id': 1, 'updated_at': '2021-05-26T14:03:03Z'}]

    mocker.patch.object(AsyncClient, 'get', return_value=mock_list_response)
    mocker.patch.object(AsyncClient, 'delete', return_value=mock_delete_response)
    await main_(
        **{
            'account_type': 'org',
            'org_name': 'test',
            'image_names': 'a,b,c',
            'timestamp_to_use': 'updated_at',
            'cut_off': '2 hours ago UTC',
            'untagged_only': 'false',
            'skip_tags': '',
            'keep_at_least': '0',
            'filter_tags': '',
            'filter_include_untagged': 'true',
            'token': 'test',
        }
    )
    captured = capsys.readouterr()
    assert (
        captured.out
        == 'The follow images are public and have more than 5000 downloads. These cannot be deleted via the Github '
        'API:\n\n\t- a:1\n\t- b:1\n\t- c:1\n\nIf you still want to delete these images, contact Github support.\n\n'
        'See https://docs.github.com/en/rest/reference/packages for more info.\n\n'
        '::set-output name=public-images-with-5000-downloads-or-more::a:1,b:1,c:1\n'
    )
