-- upgrade --
CREATE TABLE IF NOT EXISTS "telegramchat" (
    "chat_id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL /* Telegram internal chat id */,
    "chat_link" VARCHAR(64)   /* Chat @link */,
    "chat_title" VARCHAR(512) NOT NULL  /* Chat title */,
    "last_update" TIMESTAMP NOT NULL  /* last update in UTC */
);
CREATE TABLE IF NOT EXISTS "telegramuser" (
    "user_id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL /* Telegram internal user id */,
    "username" VARCHAR(64)   /* User's @username */,
    "first_name" VARCHAR(128) NOT NULL  /* User's first name */,
    "last_name" VARCHAR(128)   /* User's last name */,
    "last_update" TIMESTAMP NOT NULL  /* last update in UTC */
);
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(20) NOT NULL,
    "content" JSON NOT NULL
);
CREATE TABLE IF NOT EXISTS "poll" (
    "poll_id" CHAR(36) NOT NULL  PRIMARY KEY /* Unique poll id */,
    "timestamp" TIMESTAMP NOT NULL  DEFAULT CURRENT_TIMESTAMP /* Time of poll */,
    "poll_type" SMALLINT NOT NULL  DEFAULT 1 /* type of poll */,
    "ended" INT NOT NULL  DEFAULT 0 /* has the poll ended? */,
    "forced" INT NOT NULL  DEFAULT 0 /* was this poll forced, e.g. started by an admin? */,
    "msg_id" INT NOT NULL  /* message ID that bob was called on */,
    "poll_msg_id" INT   /* msg id of poll message */,
    "chat_id" INT NOT NULL REFERENCES "telegramchat" ("chat_id") ON DELETE RESTRICT /* chat in which the poll was started */,
    "source_id" INT NOT NULL REFERENCES "telegramuser" ("user_id") ON DELETE RESTRICT /* user who initiated the poll */,
    "target_id" INT NOT NULL REFERENCES "telegramuser" ("user_id") ON DELETE RESTRICT /* target of the poll */
);
CREATE TABLE IF NOT EXISTS "vote" (
    "vote_id" CHAR(36) NOT NULL  PRIMARY KEY /* Unique vote id */,
    "choice" SMALLINT NOT NULL  /* choice selected by user */,
    "timestamp" TIMESTAMP NOT NULL  DEFAULT CURRENT_TIMESTAMP /* Time of first vote */,
    "poll_id" CHAR(36) NOT NULL REFERENCES "poll" ("poll_id") ON DELETE RESTRICT,
    "user_id" INT NOT NULL REFERENCES "telegramuser" ("user_id") ON DELETE RESTRICT /* user that cast this vote */,
    CONSTRAINT "uid_vote_poll_id_10a5f2" UNIQUE ("poll_id", "user_id")
);
