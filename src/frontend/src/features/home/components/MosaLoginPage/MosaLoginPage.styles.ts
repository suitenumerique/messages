import styled, { css, keyframes } from 'styled-components';

export const APP_COLOR = '#F10656';
export const APP_GRADIENT_END = '#be185d';

export const float = keyframes`
  0%, 100% {
    transform: translate(0, 0) scale(1);
  }
  33% {
    transform: translate(20px, -20px) scale(1.05);
  }
  66% {
    transform: translate(-15px, 15px) scale(0.95);
  }
`;

export const fadeIn = keyframes`
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
`;

export const LoginContainer = styled.div`
  --font-heading: 'Poppins', sans-serif;
  --font-body: 'Open Sans', sans-serif;
  --app-color: ${APP_COLOR};
  --app-gradient-end: ${APP_GRADIENT_END};
  min-height: 100vh;
  font-family: var(--font-body);
  display: grid;
  grid-template-columns: 1fr 1fr;

  @media (max-width: 900px) {
    grid-template-columns: 1fr;
  }
`;

export const BrandPanel = styled.div`
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  padding: 3rem;
  overflow: hidden;

  @media (max-width: 900px) {
    display: none;
  }
`;

export const BrandBg = styled.div`
  position: absolute;
  inset: 0;
`;

export const GradientBase = styled.div`
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
`;

export const GridOverlay = styled.div`
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.16) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.16) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(
    ellipse at center,
    rgba(0, 0, 0, 1) 0%,
    rgba(0, 0, 0, 0) 70%
  );
  -webkit-mask-image: radial-gradient(
    ellipse at center,
    rgba(0, 0, 0, 1) 0%,
    rgba(0, 0, 0, 0) 70%
  );
`;

interface OrbProps {
  $delay?: string;
  $size: string;
  $top?: string;
  $bottom?: string;
  $left?: string;
  $right?: string;
}

export const Orb = styled.div<OrbProps>`
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  filter: blur(40px);
  animation: ${float} 20s ease-in-out infinite;
  width: ${(props: OrbProps) => props.$size};
  height: ${(props: OrbProps) => props.$size};
  ${(props: OrbProps) =>
    props.$top &&
    css`
      top: ${props.$top};
    `}
  ${(props: OrbProps) =>
    props.$bottom &&
    css`
      bottom: ${props.$bottom};
    `}
  ${(props: OrbProps) =>
    props.$left &&
    css`
      left: ${props.$left};
    `}
  ${(props: OrbProps) =>
    props.$right &&
    css`
      right: ${props.$right};
    `}
  ${(props: OrbProps) =>
    props.$delay &&
    css`
      animation-delay: ${props.$delay};
    `}

  @media (prefers-reduced-motion: reduce) {
    animation: none;
  }
`;

export const BrandContent = styled.div`
  position: relative;
  text-align: center;
  color: white;
  z-index: 1;
  animation: ${fadeIn} 0.6s ease-out;

  @media (prefers-reduced-motion: reduce) {
    animation: none;
  }
`;

export const AppIcon = styled.div`
  width: 88px;
  height: 88px;
  margin: 0 auto 0.75rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;

  img {
    width: 88px;
    height: 88px;
    border-radius: 50%;
    object-fit: contain;
    filter: brightness(0) invert(1);
  }
`;

export const BrandTitle = styled.h1`
  font-family: var(--font-heading);
  font-size: 2.75rem;
  font-weight: 700;
  margin: 0 0 0.75rem;
  letter-spacing: -0.02em;
`;

export const BrandTagline = styled.p`
  font-size: 1.125rem;
  opacity: 0.9;
  margin: 0;
  max-width: 300px;
  line-height: 1.6;
`;

export const BrandFooter = styled.div`
  position: absolute;
  bottom: 2.5rem;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 0.5rem;

  span {
    font-size: 0.8125rem;
    color: rgba(255, 255, 255, 0.7);
  }
`;

export const EuFlag = styled.div`
  width: 32px;
  height: 24px;
  background: rgba(255, 255, 255, 0.15);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.7);
`;

export const FormPanel = styled.div`
  background: white;
  display: flex;
  flex-direction: column;
  justify-content: center;
  position: relative;

  @media (max-width: 900px) {
    min-height: 100vh;
    justify-content: flex-start;
    overflow: hidden;
    background: linear-gradient(
      180deg,
      color-mix(in srgb, var(--app-color) 3%, white) 0%,
      white 50%
    );
  }
`;

export const LanguageSelectorWrapper = styled.div`
  position: absolute;
  top: 1.5rem;
  right: 1.5rem;
  z-index: 10;

  @media (max-width: 900px) {
    top: 1rem;
    right: 1rem;
  }
`;

export const LangSelectorContainer = styled.div`
  position: relative;
`;

export const LangButton = styled.button`
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: #4b5563;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: var(--font-body);

  &:hover {
    background: #e5e7eb;
    color: #374151;
  }

  svg {
    flex-shrink: 0;
  }
`;

export const LangDropdown = styled.div`
  position: absolute;
  top: calc(100% + 0.25rem);
  right: 0;
  min-width: 100%;
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  z-index: 10;
`;

interface LangOptionProps {
  $selected?: boolean;
}

export const LangOption = styled.button<LangOptionProps>`
  display: block;
  width: 100%;
  padding: 0.5rem 0.75rem;
  font-size: 0.8125rem;
  font-weight: 500;
  color: ${(props: LangOptionProps) =>
    props.$selected ? APP_COLOR : '#4b5563'};
  background: ${(props: LangOptionProps) =>
    props.$selected
      ? `color-mix(in srgb, ${APP_COLOR} 8%, transparent)`
      : 'transparent'};
  border: none;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
  font-family: var(--font-body);

  &:hover {
    background: #f3f4f6;
  }
`;

export const MobileAccents = styled.div`
  display: none;

  @media (max-width: 900px) {
    display: block;
    position: absolute;
    inset: 0;
    pointer-events: none;
    background-image:
      linear-gradient(
        color-mix(in srgb, var(--app-color) 8%, transparent) 1px,
        transparent 1px
      ),
      linear-gradient(
        90deg,
        color-mix(in srgb, var(--app-color) 8%, transparent) 1px,
        transparent 1px
      );
    background-size: 40px 40px;
    mask-image: radial-gradient(
      ellipse at center,
      rgba(0, 0, 0, 0.6) 0%,
      rgba(0, 0, 0, 0) 70%
    );
    -webkit-mask-image: radial-gradient(
      ellipse at center,
      rgba(0, 0, 0, 0.6) 0%,
      rgba(0, 0, 0, 0) 70%
    );
  }
`;

export const MobileHeader = styled.div`
  display: none;

  @media (max-width: 900px) {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    position: absolute;
    top: 1.25rem;
    left: 1rem;
    z-index: 10;
  }
`;

export const MobileLogo = styled.div`
  width: 28px;
  height: 28px;
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
  mask-image: url('/images/mosa/mosa.svg');
  mask-size: contain;
  mask-repeat: no-repeat;
  mask-position: center;
  -webkit-mask-image: url('/images/mosa/mosa.svg');
  -webkit-mask-size: contain;
  -webkit-mask-repeat: no-repeat;
  -webkit-mask-position: center;
`;

export const MobileBrand = styled.span`
  font-family: var(--font-heading);
  font-size: 1.25rem;
  font-weight: 700;
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
`;

export const MobileFooter = styled.div`
  display: none;

  @media (max-width: 900px) {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 2rem 1.5rem;
    margin-top: auto;

    span {
      font-size: 0.8125rem;
      color: #9ca3af;
    }
  }
`;

export const MobileEuFlag = styled.div`
  width: 32px;
  height: 24px;
  background: #e5e7eb;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
`;

export const FormContainer = styled.div`
  display: flex;
  flex-direction: column;
  padding: 2rem 3rem;
  max-width: 440px;
  margin: 0 auto;
  width: 100%;

  @media (max-width: 900px) {
    flex: 1;
    justify-content: center;
    padding: 2rem 1.5rem;
  }
`;

export const FormHeader = styled.div`
  margin-bottom: 2.5rem;
  text-align: center;

  h2 {
    font-family: var(--font-heading);
    font-size: 1.75rem;
    font-weight: 700;
    color: #111827;
    margin: 0 0 0.5rem;
  }

  p {
    font-size: 0.9375rem;
    color: #6b7280;
    margin: 0;
  }
`;

export const ProductHighlight = styled.span`
  color: var(--app-color);
`;

export const Actions = styled.div`
  display: flex;
  flex-direction: column;
  gap: 1rem;
`;

export const PrimaryButton = styled.button`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  width: 100%;
  padding: 1rem 1.5rem;
  font-size: 1rem;
  font-weight: 600;
  color: white;
  background: linear-gradient(
    135deg,
    var(--app-color) 0%,
    var(--app-gradient-end) 100%
  );
  border: none;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 4px 12px color-mix(in srgb, var(--app-color) 30%, transparent);
  font-family: var(--font-body);

  &:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px color-mix(in srgb, var(--app-color) 40%, transparent);
  }

  svg {
    flex-shrink: 0;
  }
`;

export const SignupPrompt = styled.p`
  text-align: center;
  font-size: 0.875rem;
  color: #6b7280;
  margin: 1rem 0 0;

  a {
    color: var(--app-color);
    text-decoration: none;
    font-weight: 500;

    &:hover {
      text-decoration: underline;
    }
  }
`;
