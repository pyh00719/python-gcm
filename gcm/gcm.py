import urllib
import urllib2
import json
from collections import defaultdict

GCM_URL = 'https://android.googleapis.com/gcm/send'


class GCMException(Exception): pass
class GCMMalformedJsonException(GCMException): pass
class GCMConnectionException(GCMException): pass
class GCMAuthenticationException(GCMException): pass
class GCMTooManyRegIdsException(GCMException): pass
class GCMNoCollapseKeyException(GCMException): pass
class GCMInvalidTtlException(GCMException): pass

# Exceptions from Google responses
class GCMMissingRegistrationException(GCMException): pass
class GCMMismatchSenderIdException(GCMException): pass
class GCMNotRegisteredException(GCMException): pass
class GCMMessageTooBigException(GCMException): pass


# TODO: Refactor this to be more human-readable
def group_response(response, registration_ids, key):
    # Pair up results and reg_ids
    mapping = zip(registration_ids, response['results'])
    # Filter by key
    filtered = filter(lambda x: key in x[1], mapping)
    # Only consider the value in the dict
    tupled = [(s[0], s[1][key]) for s in filtered]
    # Grouping of errors and mapping of ids
    if key is 'registration_id':
        grouping = {}
        for k, v in tupled:
            grouping[k] = v
    else:
        grouping = defaultdict(list)
        for k, v in tupled:
            grouping[v].append(k)

    if len(grouping) == 0:
        return
    return grouping


class GCM(object):

    def __init__(self, api_key):
        self.api_key = api_key

    def construct_payload(self, registration_ids, data=None, collapse_key=None,
                            delay_while_idle=False, time_to_live=None, is_json=True):
        """
        Construct the dictionary mapping of parameters.
        Encodes the dictionary into JSON if for json requests.
        Helps appending 'data.' prefix to the plaintext data: 'hello' => 'data.hello'

        :return constructed dict or JSON payload
        :raises GCMInvalidTtlException: if time_to_live is invalid
        :raises GCMNoCollapseKeyException: if collapse_key is missing when time_to_live is used
        """

        if time_to_live:
            if time_to_live > 2419200 or time_to_live < 0:
                raise GCMInvalidTtlException("Invalid time to live value")

        if is_json:
            payload = {'registration_ids': registration_ids}
            if data:
                payload['data'] = data
        else:
            payload = {'registration_id': registration_ids}
            if data:
                for k in data.keys():
                    data['data.%s' % k] = data.pop(k)
                payload.update(data)

        if delay_while_idle:
            payload['delay_while_idle'] = delay_while_idle

        if time_to_live:
            payload['time_to_live'] = time_to_live
            if collapse_key is None:
                raise GCMNoCollapseKeyException("collapse_key is required when time_to_live is provided")

        if collapse_key:
            payload['collapse_key'] = collapse_key

        if is_json:
            payload = json.dumps(payload)

        return payload

    def make_request(self, data, is_json=True):
        """
        Makes a HTTP request to GCM servers with the constructed payload

        :param data: return value from construct_payload method
        :raises GCMMalformedJsonException: if malformed JSON request found
        :raises GCMAuthenticationException: if there was a problem with authentication, invalid api key
        :raises GCMConnectionException: if GCM is screwed
        """

        headers = {
            'Authorization': 'key=%s' % self.api_key,
        }
        # Default Content-Type is defaulted to application/x-www-form-urlencoded;charset=UTF-8
        if is_json:
            headers['Content-Type'] = 'application/json'

        if not is_json:
            data = urllib.urlencode(data)
        req = urllib2.Request(GCM_URL, data, headers)

        try:
            response = urllib2.urlopen(req).read()
        except urllib2.HTTPError as e:
            if e.code == 400:
                raise GCMMalformedJsonException("The request could not be parsed as JSON")
            elif e.code == 401:
                raise GCMAuthenticationException("There was an error authenticating the sender account")
            # TODO: handle 503 and Retry-After
        except urllib2.URLError as e:
            raise GCMConnectionException("There was an internal error in the GCM server while trying to process the request")

        if is_json:
            response = json.loads(response)
        return response

    def raise_error(self, error):
        if error == 'InvalidRegistration':
            raise GCMMismatchSenderIdException("A registration ID is tied to a certain group of senders")
        elif error == 'NotRegistered':
            raise GCMNotRegisteredException("Registration id is not valid anymore")
        elif error == 'MessageTooBig':
            raise GCMMessageTooBigException("Message can't exceed 4096 bytes")

    def handle_plaintext_response(self, response):

        # Split response by line
        response_lines = response.strip().split('\n')

        # Split the first line by =
        key, value = response_lines[0].split('=')
        if key == 'Error':
            self.raise_error(value)
        else:
            if len(response_lines) == 2:
                return response_lines[1].split('=')[1]
            else:
                return

    def handle_json_response(self, response, registration_ids):
        errors = group_response(response, registration_ids, 'error')
        canonical = group_response(response, registration_ids, 'registration_id')

        info = {}
        if errors:
            info.update({'errors': errors})
        if canonical:
            info.update({'canonical': canonical})

        return info

    def plaintext_request(self, registration_id, data=None, collapse_key=None,
                            delay_while_idle=False, time_to_live=None):
        """
        Makes a plaintext request to GCM servers

        :param registration_id: string of the registration id
        :param data: dict mapping of key-value pairs of messages
        :return dict of response body from Google including multicast_id, success, failure, canonical_ids, etc
        :raises GCMMissingRegistrationException: if registration_id is not provided
        """

        if not registration_id:
            raise GCMMissingRegistrationException("Missing registration_id")

        payload = self.construct_payload(
            registration_id, data, collapse_key,
            delay_while_idle, time_to_live, False
        )

        response = self.make_request(payload, is_json=False)
        return self.handle_plaintext_response(response)

    def json_request(self, registration_ids, data=None, collapse_key=None,
                        delay_while_idle=False, time_to_live=None):
        """
        Makes a JSON request to GCM servers

        :param registration_ids: list of the registration ids
        :param data: dict mapping of key-value pairs of messages
        :return dict of response body from Google including multicast_id, success, failure, canonical_ids, etc
        :raises GCMMissingRegistrationException: if the list of registration_ids exceeds 1000 items
        """

        if not registration_ids:
            raise GCMMissingRegistrationException("Missing registration_ids")
        if len(registration_ids) > 1000:
            raise GCMTooManyRegIdsException("Exceded number of registration_ids")

        payload = self.construct_payload(
            registration_ids, data, collapse_key,
            delay_while_idle, time_to_live
        )

        response = self.make_request(payload, is_json=True)
        return self.handle_json_response(response, registration_ids)
