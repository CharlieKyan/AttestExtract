"""Bounded compatibility for the dev22 experiment and the review-only dev23 change.

Public pins below come from the fixed v0.1.0.dev22 Git tree. LF and CRLF are the
only accepted byte representations, since existing Windows archives contain both.
No archive-provided code hash can expand this allowlist. Dependencies must remain
identical and all extraction modules remain byte-identical apart from the version.

This checks compatibility, not code authenticity. The review attachment separately
pins the complete new runtime before D may execute. No legacy A/B/C replay is
permitted by the runner.
"""

from __future__ import annotations

from attestextract.errors import ConfigurationError


BASELINE_TAG = "v0.1.0.dev22"
BASELINE_COMMIT = "807cc0432bced1d78450efa46dd78358e11494ab"
COMPATIBILITY_REVISION = "dev22-review-only-v1"

DEV22_PACKAGE_HASHES = {'__init__.py': ('469b29fe536ad56559400912c38ed905b3862f94395774108e516c1dab1d4caa',
                 'e9b7410312ca481821c1c56d2a70789f82d41893b15df2f3b286cc1d23c100a5'),
 '__main__.py': ('98f7508668debacacbac9e98cf645ab376ef350c9038e54d0bfb2afb09efe823',
                 'b67c184b49bd0fada3ffc2d5c9d050318cf0bf36e3dc8c538ae4580b25348ee0'),
 'adapter_examples.py': ('28488d2bfee625e55778eb7f4ba775e9c682a205b211a7999b43ff2ca0c60f64',
                         'a3f5b5e2ac70f15e555228f8f8f5162c3e7a02299962c8cf797582fab0c65771'),
 'archive.py': ('ba55d051d2adf96f08dfb7c1985b76cbef147fc3b5862c366ccdf5fadf63411f',
                'f323914bed0da74552b24e5c10f37968c7bc6d754c5d135992a5e9d2b1d52bc6'),
 'backends.py': ('fd2453a962f095576c6e8ffc8ce95ebe78a5b7f02ee285c62263b1b5a68217e9',
                 'edc89d039693a0c80eeaaee0e148460b4b91571ca2baa5e6ae2558ab5c4bcece'),
 'capabilities.py': ('c9514d5220266ecb38486ef8a5f93406f6e59b30a29fa976c27a1fc6300a5281',
                     '1945303ce77d7950fa5daabfb01f803fdf9428099ab27e025b133c8b27a630f7'),
 'chunking.py': ('cc517422261651266f842954ae7980f5d7d4213235a10ffc21d3e9d6ee6018ba',
                 '99c9dc875860254c666ff582697b35c9eead7f5bc57718cded0414d9a0f1571d'),
 'cli.py': ('e94fd47628e9ca16ab1ac31aacd2564bb850cda85b03e1f11045b8410437e971',
            '17ba380f8c2dd8ed0c827b10f2b0d125a739c38153fea2a339e1080e3e2b2b4b'),
 'config.py': ('8b252e2ee9d67e5da57c55ef3e08eba958087d5cd05c4ba554974afafe97da6c',
               'c0ef74369167ac15c7380fde206522b709ecb445ad32a4398e58ba2dddffdbed'),
 'connection_observation.py': ('ff6e6851c184ae11a59812b6e7f090584f835a3674360008070eeff2d727ea71',
                               '50599ba7c87c6fd32e31104c753aedf3237d585792c8a63f583aecb8c65a4039'),
 'contextual_chunking.py': ('b7f328a296cbf01d955237c1afc51be1a1f7f6558ace2713dfd580a2a27fe44f',
                            'a0d5b88ce7a3fa9f311aee31971b16d0b2f81024f89186e4f31562c895e87791'),
 'd1_examples.py': ('8ab00453dfa84f04cdc9efa13a654e1a6fd7fe8163de21726e539453e3cb5b2b',
                    '0629c34df2b5abc7c77da7783fc878dd79e7e99fd647314381c83062bb604633'),
 'decision_bundle.py': ('75d1aac2e2232fa7432347fe142510730957316ff0a40894202d6a4aba7d697b',
                        '9167ff5471dcd7d73c0d4968f5355297db2809e7289818f068af68cc063429bb'),
 'decision_ledger.py': ('734baf863504003df829eb9bc77328f39a68843348dd575ecbab854d7ab6b172',
                        '308bed9bb7e6479759255c479e5f0867560a9fde260fe1483fd850d3a2a45cdb'),
 'decision_reporting.py': ('bafb13d89a633d7161ebc3f66cdbd358d15dc8d095c6ff5a2c61ab8f75da330a',
                           '8bcc35c369929e2bb1b069a604273ce8e068923804d50f5970d5624f68555f5c'),
 'decisions.py': ('028916d7c60f58a34491d8110b1a1bf1ffc027834acc1e44115f34d81afce495',
                  'f167071c1416b2dc17a707af74cc653aa00ed1b75389ee2f6ff9c4768b4f0fdf'),
 'diagnostics.py': ('f6dd9687ea6a091cadd41e1bdceaa220cbeed1fe7dde6a85d03da81fd5ec9aa1',
                    '34cf286677967e4065a05c8d9c12a01ea3f4b609faa3c313a673d303c2e350dd'),
 'documents.py': ('b8ff116c463ac50fb5eca1aed2bc52f95114a0067d15a2972b7c76fb21769f06',
                  '5eb21872a7eb062cf9742ce626315f7d22a2995898246ceaa72dd54f4c101781'),
 'envconfig.py': ('8b18c97332ef2817258bf0c8b1a0c3755a0f594fda16b38f4cdefccb812a9b7c',
                  'ee93839e6ee0353d604f32a8a216642262049b48a7c2244f3ca9df1df5a6c7ef'),
 'errors.py': ('2653b23a60582624a695b1c267d775abe6737490964b0d0e68e52ef3e569bfb6',
               '53b07d87bf73844819b7e189dfd30da83d8fc1cccff11ecb3e7079eff02d8181'),
 'evidence.py': ('112bf7fd35850382894f25547bca0db6192541463f2c44de67a119ed067adc0c',
                 'a37fac54b5592efbc58f560a25d23bbd0de3859a2e456683a7da71cde379b64c'),
 'execution.py': ('cd89ffdc24817cb50dcc8ca5d0cc756a2983457e07e2ff9b33860d435a4e535f',
                  '7f6083449f3ca29f6aeb192f1e3e0b6e7fe4e583de71d8baf3cb91e27951f1ad'),
 'execution_migration.py': ('d606ad22b8c5d907c04ddd103118907200d8744997f6179756ba6e9ae611a9d0',
                            'c1103debd9cf27ec92197872c3341b606251a2f0f406df755c421f148e5c37fe'),
 'http_diagnostics.py': ('e2f74521b3e97b8ac1031739ec8cfa9aa8a772b34d4394cdb6b9a54e6f7e9350',
                         '1fa14a78307a260c091458e671da52bf057ac823a5c1e6db524ea1a3c938bc56'),
 'identity.py': ('082d1313895056ba2bc229bd4acedf6d84a8096fbe2d611c0c1d7b4fbd763a3a',
                 'bb65c9c8d53cda2b2a3bf0fcf6503c6126d24bf2c2746986cc62d51ce9fbefde'),
 'json_validation.py': ('782f6d5feeb68f45b988b52a01db8653eaeb1b638301c859b4db42a4dd49523b',
                        '7ab9fb502f2f8d99a99cb8e2a91ebfb49559cfc9f1b638b0aa4dfb26a3a93918'),
 'measurement_syntax.py': ('416d199ac0a4ff302fb91f56db892bb1ad852cfe5b701e5477ffe74721d666f2',
                           'fd1f84ff42e6b73cd4cbb253133620ca02a6bee571e025b04b7c023547d9acf5'),
 'models.py': ('77baa15890cea972a6099b12a9270e27d260811c4b3c53b2f05e5e46cfbc2e3f',
               '130cb777b015d92af9683b490e61e865f1449c08b1811941006bdd978628c52d'),
 'normalization.py': ('1ff892be92a8a4979535b4f834dd683ac7e6d39d8767b9303604eeb1eb47d23c',
                      'a43b6961e6a7044100c32b87279aa5f8e52a21fd4ba5f045f9aff3079d8dbb9a'),
 'output_protocol.py': ('26f11d47016906fac77e790d62d421eaf07b43465d998e2c7b85ff0723497bdc',
                        '92c1ec6a172afcc8281d56002c53ac94562c5c8df4348f88661cab2de9500c70'),
 'pipeline.py': ('1539b8d79bd5a287f701cc9773691c167a74030ce2c40776fed216952be6ab87',
                 '4fa00436a75da1f6dcef3ae25b01209cddb9fc080c5c8237096f9b0974674a1d'),
 'planning.py': ('360ab2346474d35182d137b98eea704cc03d4f96d592fa6bcec113497ef716d5',
                 '30c585bd82a9f608786157244e9b564ca4ed2a86b08d53346f06957360a53b26'),
 'progress.py': ('6d11da5c7329cb0085d912ba37b91e4992bbbf1ac3e2567a378323edb0ee7215',
                 'e79bda8613c136da66bb7957b4ae70df0fdf8ebd29b703ce249954a27c7a6c9a'),
 'prompting.py': ('0f6e09eda89e48604fb9228f1da296a53423305aba4adeead6df1a200e343ed3',
                  '207c336435ef8b4fc491605fb5532897c2c9827ddc979fc223ce47762d5fb3a3'),
 'providers.py': ('b159fab4bdac85b28f392ea63cacb754b0831c4e9e8d2a4be3402ded2cc88eca',
                  '72efdcb25296963dc8157e49c815dbe6a6bace07eef574ea1b2a3b4c3583173b'),
 'registry.py': ('d3d008e89b7104f644aad998b802e2ba935441756995326ceb7b0301f7410d63',
                 '65f14bf35566d2d950a6067236a61acc29e032163787d353654c92956c49a48c'),
 'reporting.py': ('0f3c1aa56e179e05c457d6239e581c135796c7a99423bfae84403b766a79ac27',
                  '190779c5e017cd776e44e7e9c6740d063978670f92b10d923ada72cfd92693d5'),
 'responses_backend.py': ('e60a12572a9765709960f2fad3f47514af7ac5d822c06dfc0b8ec5dc0e7f913f',
                          'b57a3db9521f769c8ba991640fd3a8e8824b44a4cb088c8dfcf87d9b526cbfd9'),
 'schema_diagnostics.py': ('b789b7244cae7a6ca03a05728a9edf215cb32c55f289fdaf4e3bca1256845361',
                           'c52386853efc0e25dadea23f195d9869603871a7e174a1c714fa3f09cc829a54'),
 'storage.py': ('42b42479225e1f8c16085468692729348567fc18f26b2933391e58f02c431737',
                '64e0c1f18ae9f10e0b22675058a8f3ff06f6b5fb6c63bf4f70d2aff378c97b0f'),
 'structure.py': ('1fea540fa882baf0f25742839262d5429ac3f23015bbf5890a61f7fc0a7bc7fc',
                  '40a0fc9f17aff3bc6b700bdd0cf5fdaf86f095f8880d0ac286e341a762577f0e'),
 'structure_diagnostics.py': ('871bebb0b1ac628910b4dc5f93c862fa8cd3dd22e164b9661db0740457b43855',
                              '0095e1581018bd64fcca71a856cd271d63e661bca848e1fd8a5baee983017d46'),
 'transport.py': ('b0f51a7704f9fad3073fc01fc44ac31cbde5961b0533c001145156761eed81b6',
                  '48f5cb5e785484102960f109f1b28ffd90e18acae239e91a0a05195b06883d04'),
 'validation.py': ('0587a95981ab7a32add5b7aad3faf69147ab9cc6b27afcfb9cc37b8b1c1efb81',
                   'd983e1a5a914e09b135ed7e356b7210fa44b3f7a8731c16c2efde6cdb02fa025')}

DEV22_SCRIPT_HASHES = {'provider_experiment.py': ('ecce73219c6d38f30eb5aae6a662cb98a90e682312f4083ebeffbe140caa12c6',
                            '1d768d86dea26a5ccc651a2eef449ddd3e8a1dd403f1832d53b9ac248152a39a'),
 'provider_experiment_bundle.py': ('427da8182d0df412d8231ced64ca4cc113bfd2b139388ca18c5e57585ed54d22',
                                   'b8fa7716c4d917d726269bb2a04dfc8cf69c3e402c520609eaab0a0836f6a88f'),
 'provider_experiment_reporting.py': ('818aec4be4469ec51fac04bd5747d74cf80569799e856e2485af6dc12a7afdb0',
                                      'd3c27a207ff3710ffeea1e88c467bf72593905fe853f9792de8dd230c5acee90'),
 'provider_experiment_runner.py': ('f7d302adfa1e2120e83dba75966226e3472f8d3b5ec1fc7c3883637ac603b73b',
                                   '57dbf184d3c41659971209ed4520f5038ef349260f6b60bb7b09e334c30f205f'),
 'provider_experiment_smoke.py': ('7c8088e9a0381da38aa6d1a390f42594e9e4d9ad5b7052dd855e3760ef625737',
                                  'ac6f4a65c5ff5feda1fc09010da92bc364e5dfe64a66ad753fa3c4da908d26f9')}

DEV23_INIT_HASHES = ('b8b72e678460bc805dde1e8d2c91766cffa9d9029c6f51f418d48d91add31b8d', '42f2c2385cf8b542be9bd1ea6f716ec0cffdb1c730f672768b0d4e1a345a1045')

_RUNTIME_KEYS = {"package_version", "python", "system", "openssl", "httpx", "httpcore",
                 "pydantic", "certifi", "package_files", "experiment_scripts"}
_CODE_KEYS = {"package_version", "package_files", "experiment_scripts"}
_REQUIRED_CURRENT_SCRIPTS = set(DEV22_SCRIPT_HASHES) | {
    "provider_experiment_compat.py", "provider_experiment_review.py",
}


def _matches_pins(actual: object, pins: dict) -> bool:
    return (isinstance(actual, dict) and set(actual) == set(pins)
            and all(isinstance(actual[name], str) and actual[name] in allowed
                    for name, allowed in pins.items()))


def runtime_compatibility(frozen: dict, current: dict) -> dict:
    """Accept exact identities or the one audited review-only upgrade."""
    if frozen == current:
        return {"legacy": False, "revision": "exact", "source_version": frozen.get("package_version"),
                "current_version": current.get("package_version")}
    compatible = isinstance(frozen, dict) and isinstance(current, dict)
    compatible = compatible and set(frozen) == _RUNTIME_KEYS and set(current) == _RUNTIME_KEYS
    compatible = compatible and frozen["package_version"] == "0.1.0.dev22" and current["package_version"] == "0.1.0.dev23"
    if compatible:
        compatible = all(frozen[key] == current[key] for key in _RUNTIME_KEYS - _CODE_KEYS)
        compatible = compatible and _matches_pins(frozen["package_files"], DEV22_PACKAGE_HASHES)
        compatible = compatible and _matches_pins(frozen["experiment_scripts"], DEV22_SCRIPT_HASHES)
        current_packages = {**DEV22_PACKAGE_HASHES, "__init__.py": DEV23_INIT_HASHES}
        compatible = compatible and _matches_pins(current["package_files"], current_packages)
        scripts = current["experiment_scripts"]
        compatible = compatible and isinstance(scripts, dict)
        if compatible:
            compatible = set(scripts) == _REQUIRED_CURRENT_SCRIPTS
            # Original strict scores must remain reproducible, not silently relaxed.
            compatible = compatible and scripts.get("provider_experiment_reporting.py") in DEV22_SCRIPT_HASHES["provider_experiment_reporting.py"]
    if not compatible:
        raise ConfigurationError(
            "Engine or experiment code changed outside the supported dev22 review-only upgrade; "
            "the frozen experiment cannot be reused.")
    return {"legacy": True, "revision": COMPATIBILITY_REVISION,
            "source_version": "0.1.0.dev22", "current_version": "0.1.0.dev23",
            "baseline_tag": BASELINE_TAG, "baseline_commit": BASELINE_COMMIT}
