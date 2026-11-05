# Discord 보안봇

Python `discord.py` 기반의 서버 보안/자동관리 봇입니다.

## 포함 기능

- 슬래시 명령어: `/warn`, `/warnings`, `/timeout`, `/kick`, `/ban`, `/purge`, `/security`
- 8초 내 반복 메시지 스팸 감지 및 10분 타임아웃
- 멘션 6명 이상 도배 감지
- Discord 초대 링크 자동 삭제
- 흔한 무료 니트로/보상 피싱 패턴 링크 자동 삭제
- 3일 미만 신규 계정 로그 및 선택적 격리 역할 부여
- 20초 내 8명 이상 입장 시 레이드 의심 로그
- 운영진 처벌/삭제 로그
- SQLite 기반 경고 누적 저장

## 설치 및 실행

1. Discord Developer Portal에서 애플리케이션을 만들고 Bot을 추가합니다.
2. `Privileged Gateway Intents`에서 **Server Members Intent**와 **Message Content Intent**를 켭니다.
3. Bot을 `bot` 및 `applications.commands` scope로 서버에 초대하고, 다음 권한을 부여합니다.
   - View Channels, Send Messages, Manage Messages
   - Moderate Members, Kick Members, Ban Members
   - Manage Roles (신규 계정 격리 역할 사용 시)
4. 설치:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

5. `.env`에 토큰과 채널/역할 ID를 입력합니다.

```env
DISCORD_TOKEN=봇_토큰
LOG_CHANNEL_ID=로그채널ID
QUARANTINE_ROLE_ID=격리역할ID
MOD_ROLE_IDS=운영진역할ID1,운영진역할ID2
```

6. 실행:

```bash
python bot.py
```

토큰은 절대 GitHub나 채팅에 올리지 마세요. 봇 역할은 제재 대상 역할보다 위에 있어야 하며, 운영진 계정에는 봇보다 높은 역할을 두는 것이 안전합니다.
