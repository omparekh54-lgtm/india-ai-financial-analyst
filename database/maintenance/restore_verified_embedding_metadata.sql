-- Recover the 39 exact chunks whose model metadata was lost in collector 36783310884.
-- Before that collector, run 36772247500 attempt 2 embedded all 4,607 official chunks
-- with MiniLM; operator verified dimensions, model metadata and content checksums.
-- All 4,607 vectors remained present afterwards; the old filing upsert retained vectors
-- ONLY for unchanged content. Each selected chunk is pinned to its observed ID,
-- content SHA-256 and vector SHA-256. No unknown/later/changed vector is relabelled.
with verified_chunks(id, content_sha256, vector_sha256) as (
  values
    ('08ba6cb0-79b9-40b2-b16c-1c9fdb272207'::uuid, '5ef3ec26066d4d265bd9a72b57bc0a9fb62f1f09ca7a80a907e51bb8458479ef', '6740e5e9da4f407a4e173d2eeae62615725af9125b2dea938fccd3f8e0cdc257'),
    ('106aa88d-9200-437b-91b4-2a2c86188263'::uuid, '65097a30e2164de4913766403cdc660289b5bb319d805f1d48b31e31a93f11f2', 'dcbfd5c7533cccdd0fbb79dab8c9d10100c356c737c893ae17575a92db076ba6'),
    ('12d43c0c-7537-4a1b-8167-7ae047bf2182'::uuid, '63050811f9c8e96968d764229a519e22c55705233bf4dd5ad7b38d7abb0acd5e', '99341513b3ad579e86511f7f099d863656c0087b442f4903b0b516b9c49ba793'),
    ('14742130-9175-4baf-b29e-91f03f917f9a'::uuid, 'dae970dffbf88933aff035e90b8454dd6488218185144b6b217270b481e25657', 'af977d9745bf807b56e42ec61987493d8fe407f5ae69ebd1e68e5833860caa30'),
    ('22771c0f-6c76-4da6-aeef-e91ada88158f'::uuid, 'd0ddc9f4e63d7c57b2436c345dcb9995b63ed542cc921e2dce904a72c85cc574', 'e31af4a7c1fc1888934db3a79b39c85b3f776416fa4e007b40ab9fb0d6756d09'),
    ('24f3cef8-1de7-4e17-bee0-f67235e85d7e'::uuid, 'd9a68aa8f17e67a4f360fe0964838d16bf2bd357b92626c7233e8d6c21e56ccb', '1d91d87d440be9429ee711e682d8c007b1023fdd5e32ce42868e858f4fc187d2'),
    ('25098474-a52f-42fd-8b64-d5ed693c72a0'::uuid, '9bebe6eafb0c2958b800c8222e5dffbf1744f4aa55919f1013af35ab0f6c883d', 'af442169c614737bbf80a78b94d1ad306730fa86064ad39db732c6f61b8300e0'),
    ('2736ff4c-59b9-4558-8ceb-2dacfd03d213'::uuid, '4a6cb42f02452f013bb2c7625929b8188c7b3859df79a6b568dfe8d60ad5e0b6', '4a500bc95d2d0d59ffb15500fccd955ede7f08eb0d260e37ea5ee63caf138881'),
    ('2bf9d887-1eb0-4435-8cd6-d3cb6b6f6d10'::uuid, '897113a7c6fe2ecf7bbe9da8004058b6dacdeab4005eb5050deebfb9b63235cd', '8190025ef719452c5ea868dfce120964f40a42a1021a93620a0323fc8fb9374d'),
    ('2eb02748-46c5-465c-83ce-2c6051ddef01'::uuid, 'cdfbcb7ea2316cc83a8ca6631b72693db29f47ce00b15ccba507e37bebd3a7b6', 'd6d27a76b79ce5127b066f228bbecededcb81aa0d72c126340728d1ea6e77716'),
    ('3617d261-d493-40f6-8976-1ec05732dc92'::uuid, '366957e030c0edd00fae53b993892783a5f1fb449ae52cc121c3ae642e95463a', '25c37509f89c63348d0f07cc899d287041cb37e9edaed1e452762460d31647c7'),
    ('391941f4-04f5-45f4-b85f-a44c9ca72e4d'::uuid, '2aec3a805bb3d5de051470fa0c832eb80135f15d3fcf2ed9d700ba425d5c5b4f', 'd5c19c667d03efaf720a6a316d5d7c450fc8bb6b8b7a035c6bb9426e721d48b7'),
    ('47ebf92b-8c39-4345-8cb7-caf7eea9b1e8'::uuid, '591f9704a46d29e55eacd9eff111f673e41795d5ff88a5b64a42f2f2429fa06a', '25e6bbc68278a461a18910f4115d4fdc785456cf711448ff8429ac468f94db4d'),
    ('4aeeec14-a4af-461f-b48b-cf18b4b7a282'::uuid, '81fa83d6c09890482dc32fbaad0c973bafd392ee27a51494771bcccafbe2ac03', '777368b8fdf0610fca4ae0ecb3860639671a5d767561bd93b0505bc68bc7dd7e'),
    ('52ccaeee-9e0c-48f2-aed7-0d5a5f4a1c01'::uuid, '0a10c1627b2b67eded7dc4bfd3fe340241df2cd738fcb10e6cc6591e19730433', '037b2f548c5fb5f17aebb5c35902c67c5f177a9898ca4daad86f38290ddf1f73'),
    ('5aefcff1-5ffa-40cb-aa67-32b793ab7b25'::uuid, '6584b0cae94ab85e1180fd74d4d07df7f2c030130e04837a0385757c973b5f98', '35057725ae9475e67476abf137be278d85fdb714a35920aa29d02fe68e282e80'),
    ('5d667390-0c7c-4b61-9013-d92083ade955'::uuid, '4296108dfafa2325b96d80dbba442a3929ea6d58a3143d1bb2fbbf472f591148', 'c1fe054212f70af6c6045c6f9ae76f649a2637545f47754d3d5acd83c80dc7dd'),
    ('614217a8-0d88-41f6-86a0-cc8df293be0e'::uuid, '197afc76abbfeb685f322d7114a0b4ce14c8c997be53c42aab5d4dcb236335d4', '14348478399708c927ffc43fedac262d0e8dbe5142dae66683e66bf3cbd4d68f'),
    ('652cd8cd-1e9d-489e-aa84-8c0a96d085a9'::uuid, 'c2cde98860ff889b140f5ceaa6d36929714c15837405a7ddebbc6d94bd8f80d3', '944f2fcdc5cff1e35225b6a34eb41c2e0bc77cd186906e77c587b4d6f49e8fdf'),
    ('67a7805c-8c43-4749-aec7-7516aaa760bf'::uuid, 'e370965496fde6c68c1569ac9816363d2c1539ecb9d1cb09e713602adb99ec71', '86c2dc2e93363f72f5f557d8fc94b34d17df86cbc347d88244c59d2ed80e15ba'),
    ('67d4683b-2315-4873-b731-dc345a091fc4'::uuid, '424326f7052eee839f1a729575759b226c365dea23efa3775cbd94d95cff42a4', '8736a645763a6eb7759cdccbc26177fa3a867191615d829239f3cc3061ffe41c'),
    ('680c413b-c2aa-438e-8867-d7f135db195a'::uuid, 'd1a823ac44de9d8502ff048772e830c635e2a0f197079b5778a99ce2a2570f25', 'c6f18ce7c1330ce56da4ecfb8d7f24e5336fb9adc625d1ac597b51f9e78bd72c'),
    ('6c1ba4b7-b6da-448a-b16d-849d42153b5f'::uuid, '71b259de7e713d2201ec8281e03812edcbd7c16ce4f476a264b525e077e2f50e', 'c0c7140c237f3ca7ae92d21909b20cd2fde3cfb161986eed3f85d5b4482852d2'),
    ('6df389b5-2f9c-4368-b601-8236757db4bb'::uuid, 'ef90c72a1a466d20a3107dbb5bf01952b7423a52495ebaa0d6b48d91ca0bd832', 'ee1478a4dbcece6bfdf8a0f03ee1c6a9aa445a202f76cf14076f3e7fb108eb60'),
    ('7731c627-4ea7-4c53-be94-5ad17ad92a17'::uuid, 'ce94623d060dab01a9622a3a44b643566adbba72680d2062f3f571ad5e472e1b', '86f0f2f747c91114bdc1143bd1eb88afa9eb661a1e7ea46c0ffdf6290c84779e'),
    ('785392da-13fd-4e4f-8cc0-86fd36ea4c1a'::uuid, '5ef3ec26066d4d265bd9a72b57bc0a9fb62f1f09ca7a80a907e51bb8458479ef', '6740e5e9da4f407a4e173d2eeae62615725af9125b2dea938fccd3f8e0cdc257'),
    ('79137bde-5545-4e5b-a474-b0bfa3b3bf1b'::uuid, 'c1555f446e11b70c7c6ee75f9fa3e75efb7fbd45638dbc2e27d9d199ddcea7f7', 'aa8526e3314954ccd09bf0ba2d074b5ad0b956481fda1d8a5d078da120321423'),
    ('809bbc1e-9c90-4d5d-a7ab-59b5c936c855'::uuid, 'bf4ced42c0a00d3f09cc9f75fe1381dcf8e0da13a59db00a61ea3e8109af767d', '4e58e5eec5423db77215a5d022549d6a95891ce09e8fefad2479a51b76f29c30'),
    ('869d993a-3cc1-410b-94aa-a8b093f4d914'::uuid, '934eff20ada3ebb6e1109fa6302ef6f9d0f75f1d4a3178c570a1883516be1baf', '0a878d9f1f6765f38b78a407dc4e86910b14b2a75a5e57fb486069ed6766f83c'),
    ('996b85f8-5d85-4a62-b0aa-037fe6024b98'::uuid, 'f87db3fd4082b51ec16f8d8f4012ac8f40c6620542122fa9e58f1054a828557a', '873e89728c8a6310482adca6f8f815b9584dabb869bc75b4e834f0a5886ededb'),
    ('9e0d0794-a7c3-4344-ae5f-7b8057cc13be'::uuid, 'e037aa4ecc7a75642cd8115e2b73d966105beaad1815bf6dc6626badd3bdcb51', 'e112b9fed490a4a8ec50281aef779dd9520c93b9468271d9f5e36e8c56f38265'),
    ('b1877343-2c86-438f-90b6-9e9712f9ffb9'::uuid, 'e9a3d6d2892e86c79551ac4b46e81e169a8377a0601cd3a5d897cb66e0101b2b', '6ef6a3032a69946ae5b3137c71a3ab024cb862e7633b7dd04f0b863cb6875026'),
    ('c05de5e4-0ac6-4ba3-bd20-4785253cffb6'::uuid, '28323527bde397063341527153b0b9b16949b0a55f3bbacf3ce463dd11d5d841', '3d1800f066dd82cdca80ed6987b1c13e1a189d489f52c5607729a7801b80a95f'),
    ('ce0d5d19-b7c4-4f08-a762-1a26b6007706'::uuid, 'e23a52adbc23ba9064fe875c1a866ae9342c52194d6f6a11cd08462d8ae8eb43', '32889fd432a7ee049097ed58937774ad35bda60b89aad800ddd76b7699ae2b33'),
    ('db62a1dd-1ad5-4a09-8cd9-968513ba9254'::uuid, 'e80ef3eaaba2873bff4631bf59d715b7abe40c6e6961fd0409469c7c3efff9d4', 'd7365173bea021c09c660deeba89fdf88468a70d8acbc65e5196af83d3c36ec8'),
    ('de88136f-4789-46cb-b49b-05cce5ef0f96'::uuid, 'efe6d56be9cf1cad92eaa462ca52635be4a7790d9dde6b3fded6b096d7425984', 'c06de20792f365fbca2cd9dc137d8764f4f5c923840ce3830e15dcb3225a930d'),
    ('e9abc983-50b2-489c-b308-190306088b7f'::uuid, 'ae6ad696820b3dfed9026c78d762bec0fd0c6c891b2e5872b3acc2e8bf38cb4c', 'd801a2b6a0634b305571bf634ab17e8ff12603daa7783b9f77030f22ee7ad815'),
    ('eb01f61f-dce4-4c55-b739-f6365f4d1cf3'::uuid, 'f5c19fae0a32d3bb0a18720f810b2c93557d2ab5920d2b91730299e27c12f6da', 'f2632990ca5b294fd4fe541fd43cec57b6ba2473669432ee65f688d9e0d76a06'),
    ('ee99a8b2-0a86-4a41-9817-41ab7de1e47e'::uuid, '38c5d070b565902bc53fdac0c0ba940438383a44f19941fcc7454d11eaa118db', '0271f486bfab30e83aac087b63a79953e5bed15117237ecdd11181045957baf8')
), repaired as (
  update evidence_chunks ec
  set metadata = ec.metadata || jsonb_build_object(
    'embedding_model', 'sentence-transformers/all-MiniLM-L6-v2',
    'embedding_status', 'embedded',
    'embedding_metadata_recovered_from_run', '36772247500/attempt-2'
  )
  from sources s, verified_chunks v
  where ec.id = v.id and s.id = ec.source_id and s.source_type = 'exchange_filing'
    and ec.embedding is not null and extensions.vector_dims(ec.embedding) = 384
    and ec.metadata->>'embedding_model' is null
    and ec.metadata->>'evidence_kind' = 'deterministic_xbrl_fact_summary'
    and ec.metadata->>'content_sha256' = v.content_sha256
    and encode(sha256(convert_to(ec.content, 'UTF8')), 'hex') = v.content_sha256
    and encode(sha256(convert_to(ec.embedding::text, 'UTF8')), 'hex') = v.vector_sha256
  returning ec.id
)
select count(*) as repaired_model_metadata from repaired;
