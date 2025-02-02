import aiohttp
import discord
import jmespath


from .database import VerifyMember, DBAPI


class APIClient:
    def __init__(self, settings: DBAPI):
        self.settings = settings

    async def get_mail(self, idx: str) -> str:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.settings.token}"}
        ) as session:
            async with session.get(
                self.settings.server + self.settings.mail_endpoint.format(idx)
            ) as resp:
                data = await resp.json()
                print(f"MAIL DATA: {data}")
                print(resp.status)
        mail = jmespath.search(self.settings.mail_jmespath, data)
        return mail

    async def get_role_ids(self, member: VerifyMember) -> list[int]:
        roles = []
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.settings.token}"}
        ) as session:
            for endpoint in self.settings.role_endpoints:
                print(member)
                print(endpoint.role_endpoint.format(member.address))
                print(endpoint.role_jmespath)
                async with session.get(
                    self.settings.server + endpoint.role_endpoint.format(member.address)
                ) as resp:
                    data = await resp.json()
                    print(f"ROLE DATA: {data}")
                    role_data = jmespath.search(endpoint.role_jmespath, data)
                    for mapping in self.settings.role_mappings:
                        print(
                            f"role data: {role_data} == mapping-api_data: {mapping.api_data}"
                        )
                        if role_data == mapping.api_data:
                            roles.append(mapping.role_id)
        return roles
