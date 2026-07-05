import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { login } from '@/features/auth';
import { LANGUAGES } from '@/features/i18n/conf';

import {
  ArrowRight,
  ChevronDown,
  EuStars,
  GlobeIcon,
} from './MosaLoginPage.icons';
import {
  AccentDot,
  Actions,
  BrandBg,
  BrandContent,
  BrandFooter,
  BrandPanel,
  Divider,
  EuFlag,
  FormContainer,
  FormHeader,
  FormPanel,
  GradientBase,
  GridOverlay,
  LangButton,
  LangDropdown,
  LangOption,
  LangSelectorContainer,
  LanguageSelectorWrapper,
  LoginContainer,
  MobileAccents,
  MobileEuFlag,
  MobileFooter,
  MobileHeader,
  PrimaryButton,
  ProductHighlight,
  SignupPrompt,
} from './MosaLoginPage.styles';

interface LanguageOption {
  code: string;
  value: string;
  label: string;
}

const LANGUAGE_OPTIONS: LanguageOption[] = LANGUAGES.map((lang: [string, string]) => ({
  code: lang[0].split('-')[0].toUpperCase(),
  value: lang[0],
  label: lang[1],
}));

const LanguageSelector = () => {
  const { i18n } = useTranslation();
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const currentLang = useMemo(() => {
    const langCode = i18n.language?.split('-')[0]?.toUpperCase() || 'EN';
    return (
      LANGUAGE_OPTIONS.find(
        (l) => l.code === langCode || l.value === i18n.language
      ) || LANGUAGE_OPTIONS[0]
    );
  }, [i18n.language]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = (value: string) => {
    i18n.changeLanguage(value);
    setIsOpen(false);
  };

  return (
    <LangSelectorContainer ref={ref}>
      <LangButton onClick={() => setIsOpen(!isOpen)}>
        <GlobeIcon />
        <span>{currentLang.code}</span>
        <ChevronDown rotated={isOpen} />
      </LangButton>
      {isOpen && (
        <LangDropdown>
          {LANGUAGE_OPTIONS.map((lang) => (
            <LangOption
              key={lang.value}
              $selected={currentLang.value === lang.value}
              onClick={() => handleSelect(lang.value)}
            >
              {lang.label}
            </LangOption>
          ))}
        </LangDropdown>
      )}
    </LangSelectorContainer>
  );
};

interface MosaLoginPageProps {
  next?: string;
}

export const MosaLoginPage = ({ next }: MosaLoginPageProps) => {
  const { t } = useTranslation();

  const handleLogin = () => {
    login(next);
  };

  useEffect(() => {
    document.title = t('Sign in to Mail - mosa.cloud');
  }, [t]);

  return (
    <>
      <LoginContainer>
        <BrandPanel>
          <BrandBg>
            <GradientBase />
            <GridOverlay />
            <AccentDot
              $left='64px'
              $top='calc(50% - 160px)'
              $size='4px'
              $opacity={0.5}
            />
            <AccentDot
              $left='256px'
              $top='calc(50% - 224px)'
              $size='12px'
              $opacity={0.7}
              $delay='-1s'
            />
            <AccentDot
              $left='64px'
              $top='calc(50% + 96px)'
              $size='5px'
              $opacity={0.55}
              $delay='-2s'
            />
            <AccentDot
              $left='192px'
              $top='calc(50% + 224px)'
              $size='6px'
              $opacity={0.55}
              $delay='-3s'
            />
            <AccentDot
              $left='384px'
              $top='calc(50% + 160px)'
              $size='4px'
              $opacity={0.4}
              $delay='-4s'
            />
          </BrandBg>

          <BrandContent>
            <img src='/logos/mosa-cloud-logo-white.svg' alt='mosa.cloud' />
          </BrandContent>

          <BrandFooter>
            <EuFlag>
              <EuStars />
            </EuFlag>
            <span>{t('Built in the EU')}</span>
          </BrandFooter>
        </BrandPanel>

        <FormPanel>
          <LanguageSelectorWrapper>
            <LanguageSelector />
          </LanguageSelectorWrapper>

          <MobileAccents />

          <MobileHeader>
            <img src='/logos/mosa-cloud-logo.svg' alt='mosa.cloud' />
          </MobileHeader>

          <FormContainer>
            <FormHeader>
              <p className='eyebrow'>{t('Professional email')}</p>
              <h2>
                {t('Welcome to')} <ProductHighlight>Mail</ProductHighlight>
              </h2>
            </FormHeader>

            <Divider />

            <Actions>
              <PrimaryButton onClick={handleLogin}>
                <span>{t('Sign in with your account')}</span>
                <ArrowRight />
              </PrimaryButton>
            </Actions>

            <SignupPrompt>
              {t("Don't have an account?")}{' '}
              <a href='mailto:hi@mosa.cloud'>{t('Contact us')}</a>
            </SignupPrompt>
          </FormContainer>

          <MobileFooter>
            <MobileEuFlag>
              <EuStars />
            </MobileEuFlag>
            <span>{t('Built in the EU')}</span>
          </MobileFooter>
        </FormPanel>
      </LoginContainer>
    </>
  );
};
